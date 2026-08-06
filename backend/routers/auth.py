import logging
import os
import time
from collections import defaultdict
from typing import Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, validator

from auth import create_access_token, verify_token, verify_token_claims, generate_secure_token, get_password_hash, verify_password
from database_models import User, UserOperations
from services.email_service import send_verification_email, send_password_reset_email
from database import get_db_cursor

logger = logging.getLogger(__name__)

# --- Rate Limiter ---
class RateLimiter:
    def __init__(self):
        self.attempts = defaultdict(list)  # ip -> [timestamps]
    
    def check(self, key: str, max_attempts: int = 5, window_seconds: int = 60) -> bool:
        """Returns True if allowed, False if rate limited"""
        now = time.time()
        self.attempts[key] = [t for t in self.attempts[key] if now - t < window_seconds]
        if len(self.attempts[key]) >= max_attempts:
            return False
        self.attempts[key].append(now)
        return True

    def snapshot(self, window_seconds: int = 3600) -> list:
        """Current buckets for the admin abuse monitor. In-process only - see
        the NOTE below on why this is per-worker/per-container, not global."""
        now = time.time()
        out = []
        for key, timestamps in self.attempts.items():
            recent = [t for t in timestamps if now - t < window_seconds]
            if recent:
                out.append({
                    "key": key,
                    "attempts_in_window": len(recent),
                    "most_recent_seconds_ago": round(now - max(recent), 1),
                })
        return sorted(out, key=lambda x: x["attempts_in_window"], reverse=True)

_rate_limiter = RateLimiter()

# --- Outbound mail abuse controls ---
#
# Three endpoints make this server's SMTP account send a message to an address
# the caller supplies: /signup, /forgot-password and /resend-verification. None
# of them required credentials and none of them were throttled, which made the
# app an open mail amplifier - a loop against any one of them exhausts the
# provider's daily send quota, delivers unsolicited mail to third parties under
# the account holder's name, and gets the sending account suspended.
#
# Signup gets the looser budget on purpose: a classroom or a family shares one
# public IP, and several genuine signups from one address in an hour is normal.
# Password reset and resend have no such pattern, so they are tighter.
#
# NOTE: RateLimiter is in-process. Under `uvicorn --workers N` each worker keeps
# its own counters, so the effective limit is N x these values. Divide the env
# values by the worker count, or move the counters to Redis, when that lands.
SIGNUP_RATE_MAX_ATTEMPTS = max(1, int(os.getenv('SIGNUP_RATE_MAX_ATTEMPTS', '10')))
MAIL_RATE_MAX_ATTEMPTS = max(1, int(os.getenv('MAIL_RATE_MAX_ATTEMPTS', '5')))
MAIL_RATE_WINDOW_SECONDS = max(1, int(os.getenv('MAIL_RATE_WINDOW_SECONDS', '3600')))

# Domains that can never receive a real message. RFC 2606/6761 reserves these
# for documentation and testing; example.com publishes a null MX, so every send
# to it bounces straight back into the sending account's own inbox. Rejecting
# them also keeps junk rows out of the users table.
BLOCKED_EMAIL_DOMAINS = {
    d.strip().lower()
    for d in os.getenv(
        'BLOCKED_EMAIL_DOMAINS',
        'example.com,example.net,example.org,example.edu,test.com,localhost',
    ).split(',')
    if d.strip()
}
BLOCKED_EMAIL_SUFFIXES = ('.test', '.invalid', '.example', '.localhost', '.local')


def _guard_mail_rate(request, bucket: str, max_attempts: int) -> None:
    """429 when one IP has asked this endpoint to send too much mail.

    Bucketed per endpoint so a burst of password resets cannot lock out signup
    (and vice versa) for everyone sharing that IP.
    """
    ip = client_ip(request)
    if not _rate_limiter.check(f'mail:{bucket}:{ip}', max_attempts=max_attempts,
                               window_seconds=MAIL_RATE_WINDOW_SECONDS):
        logger.warning(
            f'Mail rate limit hit: bucket={bucket} ip={ip} '
            f'({max_attempts}/{MAIL_RATE_WINDOW_SECONDS}s)'
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail='Too many requests from this connection. Please try again later.',
        )


def _reject_undeliverable(address: str) -> None:
    """Refuse addresses that provably cannot receive mail.

    The wording used to have to avoid the words 'email' and 'username': the
    signup form substring-matched this detail string and would rewrite either
    into 'already exists'. That matcher is gone - Signup.jsx branches on the 409
    status now - so this text is free to say whatever is clearest to the user.
    """
    domain = address.rsplit('@', 1)[-1].strip().lower()
    if domain in BLOCKED_EMAIL_DOMAINS or domain.endswith(BLOCKED_EMAIL_SUFFIXES):
        logger.warning(f'Rejected signup for undeliverable domain: {domain}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='That address cannot receive mail. Please use a real inbox you can open.',
        )


def client_ip(request) -> str:
    """The real visitor IP, for rate-limit bucketing.

    request.client.host is the frontend container's address - every visitor
    looks identical - so it is only the last resort. X-Real-IP is set by the
    host nginx from $remote_addr, which the real_ip module has already restored
    from CF-Connecting-IP for Cloudflare peers only; a client cannot overwrite
    it. X-Forwarded-For is deliberately NOT trusted here: it is client-appendable
    and picking an entry out of it is guesswork.
    """
    if request is None:
        return "unknown"
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"

# Create a new router for auth endpoints
router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)

# --- Pydantic Models for clean API contracts ---

class SignupRequest(BaseModel):
    """Request model for user signup."""
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50, pattern="^[a-zA-Z0-9_]+$", description="3-50 characters, alphanumeric and underscores only")
    password: str = Field(..., min_length=8, max_length=100, description="Minimum 8 characters")
    confirm_password: str = Field(..., description="Must match password")
    
    @validator('confirm_password')
    def passwords_match(cls, v, values):
        if 'password' in values and v != values['password']:
            raise ValueError('Passwords do not match')
        return v
    
    @validator('password')
    def password_complexity(cls, v):
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        return v

class UserResponse(BaseModel):
    """Response model for user data, excluding sensitive information."""
    id: int
    email: EmailStr
    username: str
    is_verified: bool
    is_premium: bool
    is_admin: bool = False

    class Config:
        from_attributes = True # Allows mapping from ORM models or dicts

class TokenResponse(BaseModel):
    """Response model for JWT token."""
    access_token: str
    token_type: str = "bearer"

# --- Dependency for authentication ---

# This dependency will look for a bearer token in the Authorization header
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """
    Dependency to get the current user from a JWT token.
    Raises 401 Unauthorized if the token is invalid, expired, stale, or user not found.
    """
    payload = verify_token_claims(token)
    email = payload.get("sub") if payload else None
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = UserOperations.get_by_email(email)
    if not user:
        # This can happen if the user was deleted after the token was issued.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # A password change (self-service or admin-initiated) bumps token_version,
    # invalidating every token minted before it - otherwise a stolen token
    # would keep working for its full 7-day life even after the password that
    # protects the account changes.
    if payload.get("tv", 0) != (user.get("token_version") or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired, please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

# --- Authentication Endpoints ---

@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(request: SignupRequest, http_request: Request):
    """
    Handles user registration.
    Checks for existing email/username and creates a new user if they don't exist.
    """
    # Both guards run before any database work, so a refused attempt costs
    # nothing and leaves no row behind.
    _guard_mail_rate(http_request, 'signup', SIGNUP_RATE_MAX_ATTEMPTS)
    _reject_undeliverable(request.email)

    logger.info(f"Signup attempt for email: {request.email}, username: {request.username}")
    
    # Check if a user with that email or username already exists to provide a clear error.
    if UserOperations.get_by_email(request.email) or UserOperations.get_by_username(request.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email or username already exists.",
        )
    
    # Create the user in the database
    user = UserOperations.create(
        email=request.email,
        username=request.username,
        password=request.password
    )
    
    # This should not happen if the checks above are correct, but as a safeguard:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating the user.",
        )
        
    # Create verification token and send email
    try:
        token = UserOperations.create_verification_token(user['id'])
        send_verification_email(user['email'], token)
        logger.info(f"Verification email sent to {user['email']}")
    except Exception as e:
        logger.error(f"Failed to send verification email: {e}")
        # Don't fail signup if email fails, user can resend later
    
    logger.info(f"User '{user['username']}' created successfully (ID: {user['id']}).")
    return user

@router.post("/token", response_model=TokenResponse)
def login_for_access_token(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Handles user login via standard OAuth2 form data.
    Verifies credentials and returns a JWT access token.
    Note: The 'username' field accepts EITHER username OR email.
    """
    # Rate limiting - per IP...
    ip = client_ip(request)
    if not _rate_limiter.check(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Please try again later.")

    # ...and per account, so rotating source IPs can't grind one user's
    # password by spreading attempts across addresses. Keyed on the raw
    # submitted identifier (not the resolved user) so a nonexistent account
    # is throttled identically to a real one - no enumeration signal here.
    account_key = f"login-acct:{form_data.username.strip().lower()}"
    if not _rate_limiter.check(account_key, max_attempts=8, window_seconds=900):
        raise HTTPException(status_code=429, detail="Too many attempts for this account. Please try again later.")

    # Try to authenticate with either email or username
    user = None
    if '@' in form_data.username:
        # Looks like an email
        user = UserOperations.authenticate(email=form_data.username, password=form_data.password)
    else:
        # Looks like a username
        user = UserOperations.authenticate_by_username(username=form_data.username, password=form_data.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    logger.info(f"User found: {user['email']}, is_verified: {user.get('is_verified', False)}")
        
    # Optional: Check if the user's email has been verified.
    # This is a good security practice.
    if not user['is_verified']:
        logger.warning(f"Login blocked for unverified user: {user['email']}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please check your inbox for a verification link.",
        )
    
    logger.info(f"User '{user['email']}' authenticated successfully.")

    # The 'sub' (subject) of the token is the user's email. 'tv' pins the
    # token to the password_hash it was issued against - see get_current_user.
    access_token = create_access_token(data={"sub": user['email'], "tv": user.get('token_version') or 0})

    return {"access_token": access_token, "token_type": "bearer"}

# --- Social sign-in (Google / Facebook) -------------------------------------
#
# The browser proves who it is to the provider, hands us the resulting token,
# and we verify that token server-side before trusting a single field in it.
# On success this mints exactly the same JWT that password login does, so every
# protected route and the admin gate are untouched by this feature.

from services import oauth_service


class SocialAuthRequest(BaseModel):
    """A provider-issued token. Google sends a JWT; Facebook an access token."""
    token: str = Field(..., min_length=10, max_length=8192)


@router.get("/social/providers", status_code=status.HTTP_200_OK)
def social_providers():
    """Lets the UI hide buttons for providers this server has no keys for."""
    return {
        "google": oauth_service.google_enabled(),
        "facebook": oauth_service.facebook_enabled(),
    }


def _mark_verified(user_id: int) -> None:
    """A social provider vouched for the address, so release the email gate."""
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute("UPDATE users SET is_verified = TRUE WHERE id = %s", (user_id,))
    except Exception as e:
        logger.error(f"Failed to mark user {user_id} verified after social login: {e}")


def _login_or_create_social_user(profile: dict) -> User:
    """Resolves a verified social profile to an account, creating one if needed."""
    provider = profile["provider"]
    provider_user_id = profile["provider_user_id"]
    email = profile["email"]

    # 1. This identity is already linked - the common path.
    user = UserOperations.get_by_provider(provider, provider_user_id)
    if user:
        logger.info(f"Social login via {provider} for existing user {user['email']}")
        return user

    # 2. An account already owns this address. Linking is only safe because
    #    oauth_service refuses to return an unverified email - otherwise anyone
    #    could register someone else's address at a provider and inherit their
    #    account here.
    existing = UserOperations.get_by_email(email)
    if existing:
        already = existing.get("provider_user_id")
        if already and str(already) != str(provider_user_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email is already linked to a different social account.",
            )
        if not already and not UserOperations.link_provider(
            existing["id"], provider, provider_user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Could not link this account. Please sign in with your password.",
            )
        if not existing.get("is_verified"):
            _mark_verified(existing["id"])
        logger.info(f"Linked {provider} identity to existing user {email}")
        return UserOperations.get_by_id(existing["id"])

    # 3. Nobody here yet - create the account.
    seed = profile.get("name") or email.split("@")[0]
    username = UserOperations.generate_unique_username(seed)
    user = UserOperations.create_social(email, username, provider, provider_user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create your account. Please try again.",
        )
    logger.info(f"New user '{username}' created via {provider}")
    return user


@router.post("/social/{provider}", response_model=TokenResponse)
def social_login(provider: str, body: SocialAuthRequest, request: Request):
    """Verifies a provider token and returns our own JWT."""
    ip = client_ip(request)
    if not _rate_limiter.check(f"social:{ip}", max_attempts=10, window_seconds=60):
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Please try again later.",
        )

    try:
        profile = oauth_service.verify_social_token(provider, body.token)
    except oauth_service.OAuthError as e:
        logger.warning(f"Social sign-in rejected ({provider}) from {ip}: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    user = _login_or_create_social_user(profile)
    access_token = create_access_token(data={"sub": user["email"], "tv": user.get("token_version") or 0})
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_user)):
    """
    Returns the data for the currently authenticated user.
    This is a protected endpoint.
    """
    return UserResponse(
        id=current_user['id'],
        email=current_user['email'],
        username=current_user['username'],
        is_verified=current_user['is_verified'],
        is_premium=current_user['is_premium'],
        is_admin=current_user['is_admin']
    )

@router.post("/resend-verification", status_code=status.HTTP_200_OK)
def resend_verification_email(email: EmailStr, http_request: Request):
    """
    Resends the verification email. Has a 3-minute cooldown to prevent abuse.
    """
    # The cooldown below is keyed on user id, so it does nothing against a caller
    # cycling through addresses - one send each, unlimited. This is the per-IP
    # budget that actually bounds outbound volume.
    _guard_mail_rate(http_request, 'resend', MAIL_RATE_MAX_ATTEMPTS)

    user = UserOperations.get_by_email(email)
    if not user:
        # Don't reveal whether email exists for security
        return {"message": "If this email is registered, a verification link has been sent."}
    
    if user['is_verified']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already verified"
        )
    
    # Check cooldown (3 minutes)
    cooldown_seconds = UserOperations.check_verification_cooldown(user['id'])
    if cooldown_seconds > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {cooldown_seconds} seconds before resending email"
        )
    
    try:
        token = UserOperations.create_verification_token(user['id'])
        UserOperations.track_verification_email_sent(user['id'])
        send_verification_email(email, token)
        logger.info(f"Verification email resent to {email}")
        return {"message": "Verification email sent successfully"}
    except Exception as e:
        logger.error(f"Failed to resend verification email: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send verification email"
        )

@router.get("/verify-email", status_code=status.HTTP_200_OK)
def verify_email(token: str):
    """
    Verifies a user's email address using the token from the verification email.
    """
    user_id = UserOperations.verify_email_with_token(token)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token"
        )
    
    logger.info(f"Email verified for user ID: {user_id}")
    return {"message": "Email verified successfully"}

class ForgotPasswordRequest(BaseModel):
    """Request model for forgot password."""
    email: EmailStr

@router.post("/forgot-password", status_code=status.HTTP_200_OK)
def forgot_password(request: ForgotPasswordRequest, http_request: Request):
    """
    Sends a password reset email to the user.
    Always returns success to avoid revealing which emails are registered.
    """
    # Rate limited on the IP, not the address: the endpoint answers identically
    # for registered and unregistered addresses (deliberately, to avoid an
    # account-enumeration oracle), so the caller's connection is the only thing
    # left to budget.
    _guard_mail_rate(http_request, 'reset', MAIL_RATE_MAX_ATTEMPTS)

    user = UserOperations.get_by_email(request.email)
    
    # Always return success, don't reveal if email exists
    if not user:
        logger.info(f"Password reset requested for non-existent email: {request.email}")
        return {"message": "If this email is registered, a password reset link has been sent."}
    
    # Nothing to reset on a social-only account. The response below is
    # deliberately identical either way - saying "that account uses Google"
    # here would turn this endpoint into an account-enumeration oracle.
    if not user.get('password_hash'):
        logger.info(
            f"Password reset skipped for social-only account: {request.email} "
            f"({user.get('auth_provider')})"
        )
        return {"message": "If this email is registered, a password reset link has been sent."}

    try:
        token = UserOperations.create_password_reset_token(user['id'])
        send_password_reset_email(request.email, token)
        logger.info(f"Password reset email sent to {request.email}")
    except Exception as e:
        logger.error(f"Failed to send password reset email: {e}")
        # Still return success to avoid revealing email existence
    
    return {"message": "If this email is registered, a password reset link has been sent."}

class ResetPasswordRequest(BaseModel):
    """Request model for password reset."""
    token: str
    new_password: str = Field(..., min_length=8, max_length=100)
    
    @validator('new_password')
    def password_complexity(cls, v):
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        return v

@router.post("/reset-password", status_code=status.HTTP_200_OK)
def reset_password(request: ResetPasswordRequest):
    """
    Resets a user's password using a valid reset token.
    """
    user_id = UserOperations.verify_reset_token(request.token)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token"
        )
    
    # Update the password
    from auth import get_password_hash
    password_hash = get_password_hash(request.new_password)
    
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE users SET password_hash = %s, token_version = token_version + 1 WHERE id = %s",
                (password_hash, user_id)
            )
            # Delete the used token
            cursor.execute(
                "DELETE FROM password_reset_tokens WHERE user_id = %s",
                (user_id,)
            )
        
        logger.info(f"Password reset successful for user ID: {user_id}")
        return {"message": "Password reset successfully"}
    except Exception as e:
        logger.error(f"Failed to reset password: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset password"
        )

# Import get_db_cursor at the top if not already imported
from database import get_db_cursor

class ChangePasswordRequest(BaseModel):
    """Request model for changing password."""
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=100)
    
    @validator('new_password')
    def password_complexity(cls, v):
        if not any(c.isupper() for c in v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not any(c.islower() for c in v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not any(c.isdigit() for c in v):
            raise ValueError('Password must contain at least one digit')
        return v

@router.post("/change-password", status_code=status.HTTP_200_OK)
def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Changes the authenticated user's password.
    Requires the current password for verification.
    """
    from auth import verify_password, get_password_hash
    
    # A social-only account has no hash to check. Without this, bcrypt is handed
    # None and the endpoint 500s instead of explaining the situation.
    if not current_user.get('password_hash'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("This account signs in with "
                    f"{current_user.get('auth_provider', 'a social provider').title()}, "
                    "so it has no password to change."),
        )

    # Verify current password
    if not verify_password(request.current_password, current_user['password_hash']):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # Hash new password
    new_password_hash = get_password_hash(request.new_password)
    
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE users SET password_hash = %s, token_version = token_version + 1 WHERE id = %s",
                (new_password_hash, current_user['id'])
            )

        logger.info(f"Password changed successfully for user ID: {current_user['id']}")
        return {"message": "Password changed successfully. Please log in again."}
    except Exception as e:
        logger.error(f"Failed to change password: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password"
        )

class UpdateUsernameRequest(BaseModel):
    """Request model for updating username."""
    username: str = Field(..., min_length=3, max_length=50, pattern="^[a-zA-Z0-9_]+$")

@router.put("/update-username", response_model=UserResponse, status_code=status.HTTP_200_OK)
def update_username(
    request: UpdateUsernameRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Updates the authenticated user's username.
    """
    # Check if username is already taken by another user
    existing_user = UserOperations.get_by_username(request.username)
    if existing_user and existing_user['id'] != current_user['id']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already taken"
        )
    
    try:
        with get_db_cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE users SET username = %s WHERE id = %s",
                (request.username, current_user['id'])
            )
        
        # Fetch updated user
        updated_user = UserOperations.get_by_id(current_user['id'])
        if not updated_user:
            raise HTTPException(status_code=500, detail="Failed to fetch updated user")
        logger.info(f"Username updated for user ID: {current_user['id']}")
        
        return UserResponse(
            id=updated_user['id'],
            email=updated_user['email'],
            username=updated_user['username'],
            is_verified=updated_user['is_verified'],
            is_premium=updated_user['is_premium'],
            is_admin=updated_user['is_admin']
        )
    except Exception as e:
        logger.error(f"Failed to update username: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update username"
        )


class DeleteAccountRequest(BaseModel):
    """Re-authentication for account deletion.

    Password-based accounts send `password`. Social-only accounts have no hash
    to check, so they confirm by typing their own email address into
    `confirm_email` instead. One of the two is always required: a bearer token
    on its own must never be enough to erase an account, because a token can be
    lifted from a shared or stolen device while a password cannot.
    """
    password: Optional[str] = None
    confirm_email: Optional[str] = None


@router.delete("/me", status_code=status.HTTP_200_OK)
def delete_own_account(
    request: DeleteAccountRequest,
    current_user: User = Depends(get_current_user)
):
    """Permanently delete the authenticated user's account and all their data.

    Irreversible. Required by both app stores for any app that offers account
    creation, and the only honest answer to "delete my data".
    """
    from auth import verify_password
    from database_models import UserOperations
    from job_state import job_manager

    user_id = current_user['id']

    # --- Guard 1: re-authenticate -------------------------------------------
    if current_user.get('password_hash'):
        if not request.password or not verify_password(request.password, current_user['password_hash']):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password is incorrect."
            )
    else:
        submitted = (request.confirm_email or "").strip().lower()
        if submitted != (current_user.get('email') or "").lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Type your email address exactly to confirm deletion."
            )

    # --- Guard 2: never orphan the admin panel ------------------------------
    if current_user.get('is_admin') and UserOperations.count_admins() <= 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("This is the only admin account. Promote another admin before "
                    "deleting this one, or the admin panel becomes unreachable.")
        )

    # --- Guard 3: no deleting out from under a running worker ---------------
    # A generation job holds open paths inside generated_stories/<id>. rmtree-ing
    # that while it writes gives half-deleted stories and a traceback per
    # remaining scene, so wait for it rather than race it.
    if job_manager.has_active_job(user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("A story is still being generated on this account. Please wait "
                    "for it to finish, then delete your account.")
        )

    try:
        summary = UserOperations.delete_account(current_user)
    except RuntimeError as e:
        # Raised deliberately by delete_account when it stopped early and left
        # the account intact - surface its message, it is written for the user.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        logger.error(f"Account deletion failed for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account deletion failed. Nothing has been changed; please try again."
        )

    return {"message": "Your account and all of its data have been permanently deleted.", **summary}
