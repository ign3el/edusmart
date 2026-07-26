"""
Billing: Stripe checkout/webhook/portal, credit balance, promo code redemption.

Pricing itself is intentionally NOT in this file - it lives in the
subscription_plans / promo_codes DB tables (see database.py) so it can be
changed live from the admin panel with zero rebuild/restart/downtime.

Concurrency note: anything that grants credits or consumes a promo redemption
runs inside a single transaction that holds a row lock on promo_codes. Splitting
those steps across transactions (as this file used to) lets two simultaneous
requests both pass the eligibility check and both be paid out.
"""
import logging
import os
from datetime import datetime
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from database import get_db_cursor
from database_models import User
from routers.auth import get_current_user

logger = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL") or os.getenv("APP_URL") or "https://edusmart.ign3el.com"

# stripe.error.* was removed from the public API in v11 (it still resolves via a
# back-compat shim, but that shim is not guaranteed to survive). Bind the
# exception classes once so this module works on either generation of the SDK.
StripeError = getattr(stripe, "StripeError", None) or stripe.error.StripeError
SignatureVerificationError = (
    getattr(stripe, "SignatureVerificationError", None) or stripe.error.SignatureVerificationError
)

# Subscription states in which a user may not spend credits. Their balance is
# preserved - it just can't be drawn down until billing is healthy again.
SUSPENDED_STATUSES = ("past_due", "unpaid", "incomplete_expired")

router = APIRouter(prefix="/api/billing", tags=["Billing"])


# --- Pydantic models ---

class CheckoutRequest(BaseModel):
    tier_key: str
    promo_code: Optional[str] = None


class RedeemPromoRequest(BaseModel):
    code: str


# --- Internal helpers ---

def _get_plan(tier_key: str) -> Optional[dict]:
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM subscription_plans WHERE tier_key = %s AND is_active = TRUE",
            (tier_key,)
        )
        return cursor.fetchone()


def _get_promo(code: str) -> Optional[dict]:
    """Unlocked read - only safe for previewing a code (e.g. at checkout).
    Anything that pays out must use _lock_promo inside a write transaction."""
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM promo_codes WHERE code = %s AND is_active = TRUE",
            (code,)
        )
        return cursor.fetchone()


def _lock_promo(cursor, code: str) -> Optional[dict]:
    """Reads the promo row and holds an exclusive lock on it until the caller's
    transaction commits. This is what serialises concurrent redemptions of the
    same code - without it, the check-then-act in _validate_promo is a race."""
    cursor.execute(
        "SELECT * FROM promo_codes WHERE code = %s AND is_active = TRUE FOR UPDATE",
        (code,)
    )
    return cursor.fetchone()


def _validate_promo(cursor, promo: dict, user_id: int) -> None:
    """Raises HTTPException if the promo code isn't redeemable right now.

    Takes the caller's cursor rather than opening its own, so the eligibility
    check reads the same locked snapshot the payout will be written against.
    """
    if promo['expires_at'] and promo['expires_at'] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Promo code has expired")
    if promo['max_redemptions'] is not None and promo['times_redeemed'] >= promo['max_redemptions']:
        raise HTTPException(status_code=400, detail="Promo code has reached its redemption limit")

    # The column is nullable, so `already_used >= None` used to raise TypeError
    # and surface as a 500. Fall back to the schema default of 1 rather than
    # treating NULL as unlimited - fail closed when money is involved.
    per_user_cap = promo['max_redemptions_per_user']
    if per_user_cap is None:
        per_user_cap = 1

    cursor.execute(
        "SELECT COUNT(*) AS count FROM promo_redemptions WHERE code = %s AND user_id = %s",
        (promo['code'], user_id)
    )
    if cursor.fetchone()['count'] >= per_user_cap:
        raise HTTPException(status_code=400, detail="You've already used this promo code")


def _record_redemption(cursor, code: str, user_id: int,
                       credits_granted: Optional[int] = None,
                       checkout_session_id: Optional[str] = None) -> None:
    """Audit row + counter bump. Always called in the same transaction as the
    grant it accounts for, so credits can never exist without a record."""
    cursor.execute(
        "INSERT INTO promo_redemptions (code, user_id, credits_granted, stripe_checkout_session_id) "
        "VALUES (%s, %s, %s, %s)",
        (code, user_id, credits_granted, checkout_session_id)
    )
    cursor.execute("UPDATE promo_codes SET times_redeemed = times_redeemed + 1 WHERE code = %s", (code,))


def _get_or_create_stripe_customer(user: User) -> str:
    if user.get('stripe_customer_id'):
        return user['stripe_customer_id']
    customer = stripe.Customer.create(
        email=user['email'],
        name=user['username'],
        metadata={"user_id": str(user['id'])},
    )
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("UPDATE users SET stripe_customer_id = %s WHERE id = %s", (customer.id, user['id']))
    return customer.id


def _grant_credits_tx(cursor, user_id: int, amount: int, reason: str,
                      stripe_event_id: Optional[str] = None) -> None:
    """Additive credit grant on the caller's transaction."""
    cursor.execute("UPDATE users SET credits_balance = credits_balance + %s WHERE id = %s", (amount, user_id))
    cursor.execute(
        "INSERT INTO credit_transactions (user_id, delta, reason, stripe_event_id) VALUES (%s, %s, %s, %s)",
        (user_id, amount, reason, stripe_event_id)
    )


def _grant_credits(user_id: int, amount: int, reason: str, stripe_event_id: Optional[str] = None) -> None:
    """Additive credit grant - used for top-ups and promo bonuses, which stack on top of an existing balance."""
    with get_db_cursor(commit=True) as cursor:
        _grant_credits_tx(cursor, user_id, amount, reason, stripe_event_id)


def _set_subscription_credits(user_id: int, amount: int, reason: str, stripe_event_id: Optional[str] = None) -> None:
    """Non-additive: sets the balance to the tier's monthly allowance (no rollover) on activation/renewal."""
    with get_db_cursor(commit=True) as cursor:
        cursor.execute("UPDATE users SET credits_balance = %s WHERE id = %s", (amount, user_id))
        cursor.execute(
            "INSERT INTO credit_transactions (user_id, delta, reason, stripe_event_id) VALUES (%s, %s, %s, %s)",
            (user_id, amount, reason, stripe_event_id)
        )


# --- Story-generation credit gating (used by main.py, not exposed as an endpoint) ---

def check_and_reserve_credit(user_id: int) -> None:
    """Atomically debits one credit for a story generation, or raises 402 if the user is out.
    Must be called before any job-state/temp-folder creation, so a blocked request leaves no orphaned state."""
    with get_db_cursor(commit=True) as cursor:
        # A subscriber whose payment failed keeps their balance on the books but
        # can't spend it until billing is fixed. Free-tier users are unaffected -
        # they never had a subscription to fall behind on.
        cursor.execute(
            "SELECT is_admin, subscription_tier, subscription_status FROM users WHERE id = %s",
            (user_id,)
        )
        row = cursor.fetchone()

        # Admins are unmetered - they own the instance, so there is nothing to
        # bill and nothing to run out of. Enforced here rather than at each call
        # site so no future generation path can forget it, and deliberately
        # before the suspended-subscription check: an admin who also happens to
        # have a lapsed subscription is still an admin.
        if row and row.get('is_admin'):
            return

        if (row
                and row.get('subscription_tier') not in (None, '', 'free')
                and row.get('subscription_status') in SUSPENDED_STATUSES):
            raise HTTPException(
                status_code=402,
                detail="Your last subscription payment failed. Update your payment method to keep generating stories.",
            )

        cursor.execute(
            "UPDATE users SET credits_balance = credits_balance - 1 WHERE id = %s AND credits_balance > 0",
            (user_id,)
        )
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=402,
                detail="Out of story credits. Upgrade your plan or buy a top-up to keep generating.",
            )
        cursor.execute(
            "INSERT INTO credit_transactions (user_id, delta, reason) VALUES (%s, -1, 'story_generated')",
            (user_id,)
        )


def refund_credit(user_id: int, story_id: Optional[str] = None) -> None:
    """Credits back a story that failed generation outright, so the user isn't charged for a dead job."""
    with get_db_cursor(commit=True) as cursor:
        # Never debited (see check_and_reserve_credit), so never refund - that
        # would hand an admin a free credit for every failed job.
        cursor.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        if row and row.get('is_admin'):
            return

        cursor.execute("UPDATE users SET credits_balance = credits_balance + 1 WHERE id = %s", (user_id,))
        cursor.execute(
            "INSERT INTO credit_transactions (user_id, delta, reason, story_id) VALUES (%s, 1, 'generation_failed_refund', %s)",
            (user_id, story_id)
        )


# --- Public endpoints ---

@router.get("/plans")
def list_plans():
    """Public pricing list. Straight DB read, no caching - an admin edit is visible on the next request."""
    with get_db_cursor() as cursor:
        cursor.execute(
            "SELECT tier_key, display_name, price_display, credits_included, billing_mode, "
            "description, features, is_recommended "
            "FROM subscription_plans WHERE is_active = TRUE ORDER BY sort_order"
        )
        return cursor.fetchall()


@router.get("/balance")
def get_balance(current_user: User = Depends(get_current_user)):
    return {
        "credits_balance": current_user.get('credits_balance', 0),
        "subscription_tier": current_user.get('subscription_tier', 'free'),
        "subscription_status": current_user.get('subscription_status', 'inactive'),
        # Admins bypass the credit gate entirely, so showing them a number that
        # has no bearing on what they can do is just misinformation.
        "unlimited": bool(current_user.get('is_admin')),
    }


@router.post("/redeem-promo")
def redeem_promo(request: RedeemPromoRequest, current_user: User = Depends(get_current_user)):
    code = request.code.strip().upper()
    user_id = current_user['id']

    # One transaction, one row lock. The eligibility check, the credit grant, the
    # audit row and the counter bump all commit together or not at all. These used
    # to be four separate transactions with no lock, so N concurrent requests all
    # read times_redeemed=0, all passed validation, and all got paid.
    with get_db_cursor(commit=True) as cursor:
        promo = _lock_promo(cursor, code)
        if not promo:
            raise HTTPException(status_code=404, detail="Invalid promo code")

        _validate_promo(cursor, promo, user_id)

        if promo['discount_type'] != 'free_credits':
            # percent_off codes aren't redeemed here - just validated. The actual
            # discount is applied by Stripe at checkout (see /checkout below) and
            # the redemption is recorded by the webhook once payment succeeds.
            return {
                "type": "percent_off",
                "discount_value": promo['discount_value'],
                "message": f"{promo['discount_value']}% off will be applied at checkout.",
            }

        _grant_credits_tx(cursor, user_id, promo['discount_value'], 'promo_redeemed')
        _record_redemption(cursor, promo['code'], user_id, credits_granted=promo['discount_value'])

        logger.info(f"Promo {promo['code']} redeemed by user {user_id} for {promo['discount_value']} credits")
        return {
            "type": "free_credits",
            "credits_granted": promo['discount_value'],
            "message": f"{promo['discount_value']} free stories added to your account.",
        }


@router.post("/checkout")
def create_checkout_session(request: CheckoutRequest, current_user: User = Depends(get_current_user)):
    plan = _get_plan(request.tier_key)
    if not plan or not plan['stripe_price_id']:
        raise HTTPException(status_code=400, detail="This plan isn't available for purchase")

    customer_id = _get_or_create_stripe_customer(current_user)

    session_params = {
        "customer": customer_id,
        "line_items": [{"price": plan['stripe_price_id'], "quantity": 1}],
        "mode": "subscription" if plan['billing_mode'] == 'subscription' else "payment",
        "success_url": f"{FRONTEND_URL}/?billing=success",
        "cancel_url": f"{FRONTEND_URL}/?billing=cancelled",
        "metadata": {"user_id": str(current_user['id']), "tier_key": plan['tier_key']},
    }

    if request.promo_code:
        promo = _get_promo(request.promo_code.strip().upper())
        if promo and promo['discount_type'] == 'percent_off' and promo['stripe_coupon_id']:
            # Preview-only validation; the binding check happens in the webhook,
            # which holds the lock before recording the redemption.
            with get_db_cursor() as cursor:
                _validate_promo(cursor, promo, current_user['id'])
            session_params["discounts"] = [{"coupon": promo['stripe_coupon_id']}]
            session_params["metadata"]["promo_code"] = promo['code']

    try:
        session = stripe.checkout.Session.create(**session_params)
    except StripeError as e:
        logger.error(f"Stripe checkout session creation failed: {e}")
        raise HTTPException(status_code=502, detail="Payment provider error, please try again")

    return {"checkout_url": session.url}


@router.post("/portal")
def create_portal_session(current_user: User = Depends(get_current_user)):
    if not current_user.get('stripe_customer_id'):
        raise HTTPException(status_code=400, detail="No billing account found for this user")
    try:
        session = stripe.billing_portal.Session.create(
            customer=current_user['stripe_customer_id'],
            return_url=f"{FRONTEND_URL}/",
        )
    except StripeError as e:
        logger.error(f"Stripe portal session creation failed: {e}")
        raise HTTPException(status_code=502, detail="Payment provider error, please try again")
    return {"portal_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, SignatureVerificationError) as e:
        logger.warning(f"Stripe webhook signature verification failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_id = event['id']
    event_type = event['type']
    data = event['data']['object']

    # Atomic idempotency claim. The previous guard was a SELECT COUNT in its own
    # transaction followed by the handler in another, so two simultaneous
    # redeliveries of the same event both saw count=0 and both paid out. INSERT
    # IGNORE against a primary key makes the check and the claim one operation.
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            "INSERT IGNORE INTO webhook_events (event_id, event_type) VALUES (%s, %s)",
            (event_id, event_type)
        )
        claimed = cursor.rowcount == 1

    if not claimed:
        logger.info(f"Stripe event {event_id} already processed, skipping.")
        return {"status": "already_processed"}

    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(data, event_id)
        elif event_type == "invoice.paid":
            _handle_invoice_paid(data, event_id)
        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(data)
        elif event_type == "customer.subscription.updated":
            _handle_subscription_updated(data)
        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(data)
        else:
            logger.info(f"Unhandled Stripe event type: {event_type}")
    except Exception:
        # Release the claim so Stripe's retry gets a real attempt instead of
        # being silently swallowed as a duplicate.
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("DELETE FROM webhook_events WHERE event_id = %s", (event_id,))
        except Exception as cleanup_error:
            logger.error(f"Failed to release webhook claim for {event_id}: {cleanup_error}")
        logger.exception(f"Stripe webhook handler failed for {event_type} ({event_id})")
        raise

    return {"status": "ok"}


def _extract_user_id(metadata: dict, context: str) -> Optional[int]:
    """Metadata is attacker-adjacent: a checkout session created outside our own
    flow (Stripe dashboard, a stray payment link) carries no user_id. Returning
    None lets the caller ack the event; raising would 500 and make Stripe retry
    the same un-processable event for three days."""
    raw = (metadata or {}).get('user_id')
    if raw is None:
        logger.warning(f"{context}: no user_id in metadata; ignoring event")
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(f"{context}: user_id metadata {raw!r} is not an integer; ignoring event")
        return None


def _handle_checkout_completed(session: dict, event_id: str) -> None:
    metadata = session.get('metadata') or {}
    user_id = _extract_user_id(metadata, f"checkout.session.completed {session.get('id')}")
    if user_id is None:
        return

    tier_key = metadata.get('tier_key')
    plan = _get_plan(tier_key) if tier_key else None

    if session.get('mode') == 'subscription':
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                """UPDATE users SET subscription_tier = %s, subscription_status = 'active',
                   stripe_subscription_id = %s WHERE id = %s""",
                (tier_key, session.get('subscription'), user_id)
            )
        if plan:
            _set_subscription_credits(user_id, plan['credits_included'], 'subscription_grant', event_id)
    elif plan:
        # One-time payment - a credit top-up pack, stacks on top of the existing balance.
        _grant_credits(user_id, plan['credits_included'], 'topup_purchase', event_id)

    promo_code = metadata.get('promo_code')
    if promo_code:
        with get_db_cursor(commit=True) as cursor:
            # Take the same lock the redeem endpoint takes, so a discounted
            # checkout and a manual redeem can't both consume the last slot.
            if _lock_promo(cursor, promo_code):
                _record_redemption(cursor, promo_code, user_id, checkout_session_id=session.get('id'))
            else:
                logger.warning(f"Checkout used promo {promo_code} which is no longer active; redemption not recorded")

    logger.info(f"Checkout completed for user {user_id}, tier {tier_key}")


def _handle_invoice_paid(invoice: dict, event_id: str) -> None:
    subscription_id = invoice.get('subscription')
    if not subscription_id:
        return
    # The first invoice of a new subscription is already covered by checkout.session.completed.
    if invoice.get('billing_reason') == 'subscription_create':
        return
    with get_db_cursor() as cursor:
        cursor.execute("SELECT id, subscription_tier FROM users WHERE stripe_subscription_id = %s", (subscription_id,))
        user = cursor.fetchone()
    if not user:
        logger.warning(f"invoice.paid for unknown subscription {subscription_id}")
        return

    # A successful payment also clears any dunning state set by payment_failed.
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE users SET subscription_status = 'active' WHERE stripe_subscription_id = %s",
            (subscription_id,)
        )

    plan = _get_plan(user['subscription_tier'])
    if plan:
        _set_subscription_credits(user['id'], plan['credits_included'], 'monthly_reset', event_id)


def _handle_payment_failed(invoice: dict) -> None:
    """Suspend spending as soon as a renewal fails. Stripe keeps retrying the
    invoice for ~2 weeks; if one succeeds, invoice.paid flips this back to active
    and refills the allowance. Without this handler a past_due subscriber kept
    full service until the subscription was outright deleted."""
    subscription_id = invoice.get('subscription')
    if not subscription_id:
        return
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE users SET subscription_status = 'past_due' WHERE stripe_subscription_id = %s",
            (subscription_id,)
        )
        affected = cursor.rowcount
    logger.warning(f"invoice.payment_failed: subscription {subscription_id} marked past_due ({affected} user row(s))")


def _handle_subscription_updated(subscription: dict) -> None:
    """Mirror Stripe's own status, so a dunning/pause/cancel-at-period-end state
    that never produces an invoice event still lands locally."""
    status = subscription.get('status')
    if not status:
        return
    local_status = 'active' if status in ('active', 'trialing') else status
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE users SET subscription_status = %s WHERE stripe_subscription_id = %s",
            (local_status, subscription['id'])
        )
    logger.info(f"Subscription {subscription['id']} status synced to '{local_status}'")


def _handle_subscription_deleted(subscription: dict) -> None:
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            """UPDATE users SET subscription_tier = 'free', subscription_status = 'inactive',
               stripe_subscription_id = NULL WHERE stripe_subscription_id = %s""",
            (subscription['id'],)
        )
