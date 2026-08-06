"""
Clean implementation of authentication utilities:
- JWT token generation and verification
- Password hashing and verification with bcrypt
"""
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not installed, use system environment variables
    pass

from jose import JWTError, jwt
import bcrypt

# --- Configuration ---
# JWT_SECRET must be set in every environment. Failing loudly here beats
# silently signing tokens with a public, hardcoded key if the env var is
# ever missing (e.g. a redeploy that drops the .env mount).
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET environment variable is not set. Refusing to start: "
        "signing tokens with a default key is not safe."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# --- Password Hashing ---
def get_password_hash(password: str) -> str:
    """
    Hashes a password using bcrypt.
    
    Handles the 72-byte limit of the bcrypt algorithm by truncating
    the password bytes before hashing.
    """
    # Encode to UTF-8 bytes
    password_bytes = password.encode('utf-8')
    # Truncate to 72 bytes (bcrypt's hard limit)
    truncated_bytes = password_bytes[:72]
    # Generate salt and hash
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(truncated_bytes, salt)
    # Return as string
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plain-text password against a hashed password.
    
    Uses the same truncation logic as get_password_hash to ensure consistency.
    """
    # Encode and truncate the plain password
    password_bytes = plain_password.encode('utf-8')
    truncated_bytes = password_bytes[:72]
    # Encode the stored hash back to bytes
    hashed_bytes = hashed_password.encode('utf-8')
    # Verify
    return bcrypt.checkpw(truncated_bytes, hashed_bytes)

# --- JWT Token Handling ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Creates a new JWT access token containing the provided data.
    
    Args:
        data: A dictionary of claims to include in the token (e.g., {'sub': user_email}).
        expires_delta: An optional timedelta for when the token should expire. 
                       Defaults to ACCESS_TOKEN_EXPIRE_MINUTES.
                       
    Returns:
        The encoded JWT as a string.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token_claims(token: str) -> Optional[dict]:
    """
    Verifies a JWT token and returns its full claim set (None if invalid/expired).
    Used where a caller needs more than the subject - e.g. checking the
    embedded 'tv' (token_version) claim against the user's current value.
    """
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except JWTError:
        # This catches various errors like invalid signature, expired token, etc.
        return None


def verify_token(token: str) -> Optional[str]:
    """
    Verifies a JWT token.

    If the token is valid and not expired, it returns the subject ('sub') claim.
    Otherwise, it returns None.
    """
    payload = verify_token_claims(token)
    if payload is None:
        return None
    # 'sub' (subject) is a standard JWT claim. We use it for the user's identifier (e.g., email).
    return payload.get("sub")

# --- Generic Token Generation ---
def generate_secure_token() -> str:
    """
    Generates a cryptographically secure, URL-safe random token.
    This can be used for email verification links, password reset tokens, etc.
    """
    return secrets.token_urlsafe(32)