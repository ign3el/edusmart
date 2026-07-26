"""
Social identity verification for Google and Facebook sign-in.

Everything here answers one question: "did this token really come from the
provider, for OUR app, about this person?" Nothing in this module trusts a
value the browser sent us - the browser is where the attacker lives.

Returns a normalised profile dict so the router doesn't care which provider
it's talking to:
    {provider, provider_user_id, email, email_verified, name, picture}
"""

import logging
import os
from typing import Any, Dict

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID", "").strip()
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "").strip()

# Google mints tokens under both spellings; both are legitimate.
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

FB_API = "https://graph.facebook.com/v19.0"
HTTP_TIMEOUT = 10.0


class OAuthError(Exception):
    """A social token could not be verified. The message is safe to show a user."""


def google_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID)


def facebook_enabled() -> bool:
    return bool(FACEBOOK_APP_ID and FACEBOOK_APP_SECRET)


def verify_google_token(token: str) -> Dict[str, Any]:
    """
    Verifies a Google ID token (JWT) end to end.

    verify_oauth2_token checks the RSA signature against Google's published
    keys, the expiry, and - critically - that `aud` is OUR client ID. Without
    that audience check, an ID token issued to any other Google app would be
    accepted here, which is a complete authentication bypass.
    """
    if not google_enabled():
        raise OAuthError("Google sign-in is not configured on this server.")

    try:
        claims = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        # Bad signature, expired, or wrong audience all land here.
        logger.warning(f"Google token rejected: {e}")
        raise OAuthError("Google sign-in failed. Please try again.")

    if claims.get("iss") not in GOOGLE_ISSUERS:
        logger.warning(f"Google token had unexpected issuer: {claims.get('iss')}")
        raise OAuthError("Google sign-in failed. Please try again.")

    email = (claims.get("email") or "").lower().strip()
    if not email:
        raise OAuthError("Your Google account did not share an email address.")

    # An unverified email must never be linked to an existing account: anyone
    # could register someone else's address at the provider and inherit it.
    if not claims.get("email_verified"):
        raise OAuthError("Your Google email address is not verified.")

    return {
        "provider": "google",
        "provider_user_id": str(claims["sub"]),
        "email": email,
        "email_verified": True,
        "name": claims.get("name") or "",
        "picture": claims.get("picture") or "",
    }


def verify_facebook_token(token: str) -> Dict[str, Any]:
    """
    Verifies a Facebook user access token.

    Facebook has no signed ID token to check offline, so this takes two calls:
    debug_token to prove the token was minted for THIS app (a token from any
    other Facebook app would otherwise be accepted), then /me for the profile.
    """
    if not facebook_enabled():
        raise OAuthError("Facebook sign-in is not configured on this server.")

    app_token = f"{FACEBOOK_APP_ID}|{FACEBOOK_APP_SECRET}"

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            debug = client.get(
                f"{FB_API}/debug_token",
                params={"input_token": token, "access_token": app_token},
            )
            debug.raise_for_status()
            data = debug.json().get("data", {})

            if not data.get("is_valid"):
                raise OAuthError("Facebook sign-in failed. Please try again.")

            # The whole point of this call.
            if str(data.get("app_id")) != FACEBOOK_APP_ID:
                logger.warning(
                    f"Facebook token was issued for app {data.get('app_id')}, not ours"
                )
                raise OAuthError("Facebook sign-in failed. Please try again.")

            profile = client.get(
                f"{FB_API}/me",
                params={"fields": "id,name,email", "access_token": token},
            )
            profile.raise_for_status()
            me = profile.json()
    except OAuthError:
        raise
    except httpx.HTTPError as e:
        logger.error(f"Facebook API call failed: {e}")
        raise OAuthError("Could not reach Facebook. Please try again.")

    email = (me.get("email") or "").lower().strip()
    if not email:
        # Facebook accounts can be phone-only, or the user can decline the
        # email permission. There is no account to create without an address.
        raise OAuthError(
            "Your Facebook account did not share an email address. "
            "Please sign up with your email instead."
        )

    return {
        "provider": "facebook",
        "provider_user_id": str(me["id"]),
        "email": email,
        # Facebook only returns an address it considers confirmed.
        "email_verified": True,
        "name": me.get("name") or "",
        "picture": f"{FB_API}/{me['id']}/picture?type=large",
    }


VERIFIERS = {
    "google": verify_google_token,
    "facebook": verify_facebook_token,
}


def verify_social_token(provider: str, token: str) -> Dict[str, Any]:
    """Dispatches to the right verifier. Unknown providers are rejected."""
    verifier = VERIFIERS.get(provider)
    if not verifier:
        raise OAuthError("Unsupported sign-in provider.")
    return verifier(token)
