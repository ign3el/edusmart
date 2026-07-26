"""
Database models and operations for users and stories.
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging
import json
import mysql.connector

from database import get_db_cursor
from auth import get_password_hash, verify_password, generate_secure_token

logger = logging.getLogger(__name__)

# Use Dict instead of TypedDict to avoid required field errors
User = Dict[str, Any]

class UserOperations:
    @staticmethod
    def create(email: str, username: str, password: str) -> Optional[User]:
        """Creates a new user in the database."""
        password_hash = get_password_hash(password)
        try:
            with get_db_cursor(commit=True) as cursor:
                query = "INSERT INTO users (email, username, password_hash) VALUES (%s, %s, %s)"
                cursor.execute(query, (email.lower(), username, password_hash))
                
                if cursor.rowcount == 0:
                    logger.error("User creation failed unexpectedly (rowcount is 0).")
                    return None

                user_id = cursor.lastrowid
                logger.info(f"User '{username}' created with ID: {user_id}")
                
                # Construct a partial User object. This is more efficient than calling get_by_id.
                new_user: User = {
                    "id": user_id,
                    "email": email.lower(),
                    "username": username,
                    "is_verified": False,
                    "is_premium": False,
                    "is_admin": False,
                }
                return new_user
        except mysql.connector.IntegrityError:
            logger.warning(f"IntegrityError on user creation for '{username}'. Likely a duplicate email or username.")
            return None
        except mysql.connector.Error as err:
            logger.error(f"Database error during user creation for '{username}': {err}")
            return None

    @staticmethod
    def get_by_email(email: str) -> Optional[User]:
        """Retrieves a user by their email address."""
        try:
            with get_db_cursor() as cursor:
                query = "SELECT * FROM users WHERE email = %s"
                cursor.execute(query, (email.lower(),))
                return cursor.fetchone()
        except mysql.connector.Error as err:
            logger.error(f"Database error getting user by email: {err}")
            return None

    @staticmethod
    def get_by_username(username: str) -> Optional[User]:
        """Retrieves a user by their username."""
        try:
            with get_db_cursor() as cursor:
                query = "SELECT * FROM users WHERE username = %s"
                cursor.execute(query, (username,))
                return cursor.fetchone()
        except mysql.connector.Error as err:
            logger.error(f"Database error getting user by username: {err}")
            return None

    @staticmethod
    def get_by_id(user_id: int) -> Optional[User]:
        """Retrieves a user by their ID."""
        try:
            with get_db_cursor() as cursor:
                query = "SELECT * FROM users WHERE id = %s"
                cursor.execute(query, (user_id,))
                return cursor.fetchone()
        except mysql.connector.Error as err:
            logger.error(f"Database error getting user by ID: {err}")
            return None

    @staticmethod
    def authenticate(email: str, password: str) -> Optional[User]:
        """Authenticates a user by email and password. Returns the user object if successful."""
        user = UserOperations.get_by_email(email)
        if not user:
            return None
        
        # A social-only account has no password hash. bcrypt raises on None,
        # which would turn a wrong-form login into a 500 on a public endpoint
        # and leak the fact that the account exists.
        if not user.get('password_hash'):
            return None

        if not verify_password(password, user['password_hash']):
            return None
            
        return user
    
    @staticmethod
    def authenticate_by_username(username: str, password: str) -> Optional[User]:
        """Authenticates a user by username and password. Returns the user object if successful."""
        user = UserOperations.get_by_username(username)
        if not user:
            return None
        
        # A social-only account has no password hash. bcrypt raises on None,
        # which would turn a wrong-form login into a 500 on a public endpoint
        # and leak the fact that the account exists.
        if not user.get('password_hash'):
            return None

        if not verify_password(password, user['password_hash']):
            return None
            
        return user

    # --- Social identity (Google / Facebook) --------------------------------
    #
    # `auth_provider` records the social provider linked to this account, or
    # 'local' when there is none. `provider_user_id` is that provider's stable
    # user id. The pair carries a UNIQUE index, so one social identity can
    # never be attached to two accounts. One social provider per account is
    # supported; a second would need a separate user_identities table.

    @staticmethod
    def get_by_provider(provider: str, provider_user_id: str) -> Optional[User]:
        """Finds the account already linked to this social identity."""
        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM users WHERE auth_provider = %s AND provider_user_id = %s",
                    (provider, provider_user_id),
                )
                return cursor.fetchone()
        except mysql.connector.Error as err:
            logger.error(f"Database error getting user by provider: {err}")
            return None

    @staticmethod
    def generate_unique_username(seed: str) -> str:
        """Builds a username the DB will accept - it is UNIQUE NOT NULL."""
        import re
        import secrets

        base = re.sub(r"[^a-zA-Z0-9_]", "", (seed or "").replace(" ", "_"))
        base = base.strip("_").lower()[:24]
        if len(base) < 3:
            base = "user"

        candidate = base
        for _ in range(20):
            if not UserOperations.get_by_username(candidate):
                return candidate
            candidate = f"{base}{secrets.randbelow(10000)}"
        return f"{base}{secrets.token_hex(4)}"

    @staticmethod
    def create_social(email: str, username: str, provider: str,
                      provider_user_id: str) -> Optional[User]:
        """
        Creates an account backed by a social identity instead of a password.

        is_verified is TRUE because the provider already proved the address -
        re-sending our own verification email would be theatre.
        """
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(
                    "INSERT INTO users (email, username, password_hash, auth_provider, "
                    "provider_user_id, is_verified) VALUES (%s, %s, NULL, %s, %s, TRUE)",
                    (email.lower(), username, provider, provider_user_id),
                )
                user_id = cursor.lastrowid
            logger.info(f"Social user '{username}' created via {provider} (ID: {user_id})")
            # Re-read so the caller gets DB defaults (credits_balance, tier) too.
            return UserOperations.get_by_id(user_id)
        except mysql.connector.IntegrityError as err:
            logger.warning(f"IntegrityError creating social user '{username}': {err}")
            return None
        except mysql.connector.Error as err:
            logger.error(f"Database error creating social user '{username}': {err}")
            return None

    @staticmethod
    def link_provider(user_id: int, provider: str, provider_user_id: str) -> bool:
        """
        Attaches a social identity to an existing account.

        The `provider_user_id IS NULL` clause means an account already linked to
        one identity is never silently re-pointed at another. The result is
        confirmed by re-reading the row rather than trusting cursor.rowcount,
        which reports rows CHANGED and would read 0 for a no-op update.
        """
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE users SET auth_provider = %s, provider_user_id = %s "
                    "WHERE id = %s AND provider_user_id IS NULL",
                    (provider, provider_user_id, user_id),
                )
            user = UserOperations.get_by_id(user_id)
            linked = bool(
                user
                and user.get("auth_provider") == provider
                and str(user.get("provider_user_id")) == str(provider_user_id)
            )
            if linked:
                logger.info(f"Linked {provider} identity to user ID {user_id}")
            else:
                logger.warning(
                    f"Refused to link {provider} to user ID {user_id}: "
                    "account already has a different linked identity"
                )
            return linked
        except mysql.connector.Error as err:
            logger.error(f"Database error linking provider for user {user_id}: {err}")
            return False

    @staticmethod
    def create_verification_token(user_id: int) -> str:
        """Creates and stores a new email verification token for a user."""
        token = generate_secure_token()
        expires_at = datetime.utcnow() + timedelta(hours=24)
        
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("DELETE FROM email_verifications WHERE user_id = %s", (user_id,))
                query = "INSERT INTO email_verifications (user_id, token, expires_at) VALUES (%s, %s, %s)"
                cursor.execute(query, (user_id, token, expires_at))
            return token
        except mysql.connector.Error as err:
            logger.error(f"Database error creating verification token: {err}")
            raise

    @staticmethod
    def set_verified(user_id: int) -> bool:
        """Marks a user's email as verified in the database."""
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute("UPDATE users SET is_verified = TRUE WHERE id = %s", (user_id,))
                rows_affected = cursor.rowcount
                logger.info(f"set_verified for user {user_id}: {rows_affected} rows updated")
                return rows_affected > 0
        except mysql.connector.Error as err:
            logger.error(f"Database error setting verified status: {err}")
            return False

    @staticmethod
    def verify_email_with_token(token: str) -> Optional[int]:
        """
        Verifies an email token. If valid, marks user as verified and deletes the token.
        Returns the user_id if successful, otherwise None.
        """
        try:
            with get_db_cursor(commit=True) as cursor:
                # Check if token exists and is not expired
                query = "SELECT user_id FROM email_verifications WHERE token = %s AND expires_at > NOW()"
                cursor.execute(query, (token,))
                result = cursor.fetchone()
                
                if not result:
                    logger.warning(f"Verification token not found or expired: {token[:20]}...")
                    return None
                    
                user_id = result['user_id']
                logger.info(f"Found verification token for user ID: {user_id}")
                
                # Update user's is_verified status.
                #
                # The result is NOT checked against cursor.rowcount. MySQL
                # reports *changed* rows, not matched rows, so a user who is
                # already verified - anyone who opens the link twice, or whose
                # mail client prefetches it - produces 0 and used to be told
                # verification had failed, with the token left undeleted so the
                # retry failed the same way. The token was already proven valid
                # and unexpired above; setting a flag that is already set is
                # success, not failure.
                cursor.execute("UPDATE users SET is_verified = TRUE WHERE id = %s", (user_id,))

                cursor.execute("DELETE FROM email_verifications WHERE token = %s", (token,))
                logger.info(f"✅ Email successfully verified for user ID: {user_id}")
                return user_id
        except mysql.connector.Error as err:
            logger.error(f"Database error verifying token: {err}")
            return None
    
    @staticmethod
    def create_password_reset_token(user_id: int) -> str:
        """Creates and stores a new password reset token for a user."""
        token = generate_secure_token()
        expires_at = datetime.utcnow() + timedelta(hours=1)  # 1 hour expiry
        
        try:
            with get_db_cursor(commit=True) as cursor:
                # Delete any existing tokens for this user
                cursor.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (user_id,))
                query = "INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)"
                cursor.execute(query, (user_id, token, expires_at))
            return token
        except mysql.connector.Error as err:
            logger.error(f"Database error creating password reset token: {err}")
            raise
    
    @staticmethod
    def verify_reset_token(token: str) -> Optional[int]:
        """
        Verifies a password reset token and returns the user_id if valid.
        Does not delete the token - that should be done after password is successfully reset.
        """
        try:
            with get_db_cursor() as cursor:
                query = "SELECT user_id FROM password_reset_tokens WHERE token = %s AND expires_at > NOW()"
                cursor.execute(query, (token,))
                result = cursor.fetchone()
                return result['user_id'] if result else None
        except mysql.connector.Error as err:
            logger.error(f"Database error verifying reset token: {err}")
            return None
    
    @staticmethod
    def track_verification_email_sent(user_id: int) -> None:
        """Records when a verification email was sent for cooldown tracking."""
        try:
            with get_db_cursor(commit=True) as cursor:
                cursor.execute(
                    "UPDATE users SET last_verification_sent = NOW() WHERE id = %s",
                    (user_id,)
                )
        except mysql.connector.Error as err:
            logger.error(f"Database error tracking verification email: {err}")
    
    @staticmethod
    def check_verification_cooldown(user_id: int) -> int:
        """
        Checks if a user is in the cooldown period for resending verification emails.
        Returns the number of seconds remaining in cooldown, or 0 if no cooldown.
        """
        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    "SELECT last_verification_sent FROM users WHERE id = %s",
                    (user_id,)
                )
                result = cursor.fetchone()
                
                if not result or not result['last_verification_sent']:
                    return 0
                
                last_sent = result['last_verification_sent']
                cooldown_duration = timedelta(minutes=3)
                time_since_last = datetime.utcnow() - last_sent
                
                if time_since_last < cooldown_duration:
                    remaining = cooldown_duration - time_since_last
                    return int(remaining.total_seconds())
                
                return 0
        except mysql.connector.Error as err:
            logger.error(f"Database error checking verification cooldown: {err}")
            return 0

class StoryOperations:

    @staticmethod

    def save_story(user_id: int, story_id: str, name: str, story_data: dict, is_public: bool = False) -> bool:

        """Save a story for a specific user.

        `is_public` is the consent tick from the save dialog: it makes this story
        discoverable to other users who upload the same document. Default False -
        a caller that forgets the argument shares nothing.
        """

        try:

            with get_db_cursor(commit=True) as cursor:

                cursor.execute(

                    """INSERT INTO user_stories (user_id, story_id, name, story_data, is_public) 

                       VALUES (%s, %s, %s, %s, %s)

                       ON DUPLICATE KEY UPDATE name = VALUES(name), story_data = VALUES(story_data), is_public = VALUES(is_public), updated_at = CURRENT_TIMESTAMP""",

                    (user_id, story_id, name, json.dumps(story_data), 1 if is_public else 0)

                )

                return True

        except mysql.connector.Error as e:

            logger.error(f"Error saving story for user {user_id}: {e}")

            return False

    

    @staticmethod

    def get_user_stories(user_id: int) -> list:

        """Get all stories for a specific user."""

        try:

            with get_db_cursor() as cursor:

                query = """

                    SELECT id, story_id, name, created_at, updated_at, is_public 

                    FROM user_stories 

                    WHERE user_id = %s 

                    ORDER BY updated_at DESC

                """

                cursor.execute(query, (user_id,))

                stories = cursor.fetchall()

                for story in stories:

                    if story.get('created_at'):

                        story['saved_at'] = str(int(story['created_at'].timestamp() * 1000))

                        story['created_at'] = story['created_at'].isoformat()

                    if story.get('updated_at'):

                        story['updated_at'] = story['updated_at'].isoformat()

                return stories

        except mysql.connector.Error as e:

            logger.error(f"Error getting stories for user {user_id}: {e}")

            return []



    @staticmethod

    def get_all_stories() -> list:

        """(Admin only) Gets all stories from all users, including orphaned stories without owners."""

        try:

            with get_db_cursor() as cursor:

                query = """
                    SELECT s.id, s.story_id, s.name, s.created_at, s.updated_at,
                           s.is_public,
                           COALESCE(u.username, 'Unknown User') as username
                    FROM user_stories s
                    LEFT JOIN users u ON s.user_id = u.id
                    ORDER BY s.updated_at DESC
                """

                cursor.execute(query)

                stories = cursor.fetchall()

                for story in stories:

                    if story.get('created_at'):

                        story['saved_at'] = str(int(story['created_at'].timestamp() * 1000))

                        story['created_at'] = story['created_at'].isoformat()

                    if story.get('updated_at'):

                        story['updated_at'] = story['updated_at'].isoformat()

                return stories

        except mysql.connector.Error as e:

            logger.error(f"Error getting all stories for admin: {e}")

            return []

    

    @staticmethod

    def get_story(story_id: str, user: User, allow_public: bool = False) -> Optional[Dict[str, Any]]:

        # `allow_public=True` also returns a story whose owner made it
        # discoverable. Pass it only from read paths: it is deliberately absent
        # from delete_story, so shared never means editable.

        """

        Gets a specific story. Admins can get any story, while regular users can only get their own.

        """

        try:

            with get_db_cursor() as cursor:

                query = "SELECT us.* FROM user_stories us WHERE us.story_id = %s"

                params = [story_id]

                

                if not user.get('is_admin'):

                    query += " AND (us.user_id = %s OR (%s = 1 AND us.is_public = 1))"

                    params.append(user['id'])

                    params.append(1 if allow_public else 0)

                    

                cursor.execute(query, tuple(params))

                story = cursor.fetchone()

                

                if not story:

                    return None

                    

                if story.get('story_data') and isinstance(story['story_data'], str):

                    story['story_data'] = json.loads(story['story_data'])

                if story.get('created_at'):

                    story['saved_at'] = str(int(story['created_at'].timestamp() * 1000))



                return story

        except mysql.connector.Error as e:

            logger.error(f"Error getting story {story_id}: {e}")

            return None

    

    @staticmethod

    def resolve_visible_duplicate(story_ids: list, user: User) -> Optional[Dict[str, Any]]:

        """Pick the one saved story from a hash match that `user` may be told about.

        The hash scan matches bytes on disk and knows nothing about ownership, so
        it happily returns another account's story. Attribution is resolved here,
        against MySQL, with a real JOIN - every caller used to hardcode the
        *viewer's* username as the creator, which is why a duplicate always
        claimed to have been made by whoever was looking at it.

        Order: the viewer's own copy first, then the most recently saved copy
        whose owner ticked "make discoverable". A private story belonging to
        somebody else returns None - not even its title - because the mere
        existence of a match reveals who else uploaded that document.
        """

        if not story_ids:

            return None

        try:

            with get_db_cursor() as cursor:

                placeholders = ", ".join(["%s"] * len(story_ids))

                is_admin = 1 if user.get('is_admin') else 0

                cursor.execute(f"""

                    SELECT s.story_id, s.name, s.created_at, s.is_public, s.user_id,

                           COALESCE(u.username, 'Unknown user') AS username,

                           (s.user_id = %s) AS is_own

                    FROM user_stories s

                    LEFT JOIN users u ON u.id = s.user_id

                    WHERE s.story_id IN ({placeholders})

                      AND (s.user_id = %s OR s.is_public = 1 OR %s = 1)

                    ORDER BY is_own DESC, s.created_at DESC

                    LIMIT 1

                """, (user['id'],) + tuple(story_ids) + (user['id'], is_admin))

                return cursor.fetchone()

        except mysql.connector.Error as e:

            logger.error(f"Error resolving duplicate owner: {e}")

            return None



    @staticmethod

    def is_public_story(story_id: str) -> bool:

        """True if this saved story's owner made it discoverable."""

        try:

            with get_db_cursor() as cursor:

                cursor.execute(

                    "SELECT 1 AS ok FROM user_stories WHERE story_id = %s AND is_public = 1",

                    (story_id,)

                )

                return cursor.fetchone() is not None

        except mysql.connector.Error as e:

            logger.error(f"Error checking visibility of story {story_id}: {e}")

            return False



    @staticmethod

    def set_visibility(story_id: str, user: User, is_public: bool) -> bool:

        """Owner (or admin) turns discoverability on or off.

        Ownership is checked with a SELECT rather than trusted to the UPDATE's
        rowcount: MySQL reports *changed* rows, so re-sending the value a story
        already has affects 0 rows and would read as "not yours".

        Unsharing is not retroactive. It removes the story from future duplicate
        checks; it cannot reach into a session that already loaded it.
        """

        try:

            with get_db_cursor(commit=True) as cursor:

                cursor.execute("SELECT user_id FROM user_stories WHERE story_id = %s", (story_id,))

                row = cursor.fetchone()

                if not row:

                    return False

                if not user.get('is_admin') and row['user_id'] != user['id']:

                    return False

                cursor.execute(

                    "UPDATE user_stories SET is_public = %s WHERE story_id = %s",

                    (1 if is_public else 0, story_id)

                )

                return True

        except mysql.connector.Error as e:

            logger.error(f"Error setting visibility of story {story_id}: {e}")

            return False



    @staticmethod

    def delete_story(story_id: str, user: User) -> bool:

        """

        Deletes a story. Admins can delete any story, while regular users can only delete their own.

        """

        try:

            with get_db_cursor(commit=True) as cursor:

                query = "DELETE FROM user_stories WHERE story_id = %s"

                params = [story_id]



                if not user.get('is_admin'):

                    query += " AND user_id = %s"

                    params.append(user['id'])



                cursor.execute(query, tuple(params))

                return cursor.rowcount > 0

        except mysql.connector.Error as e:

            logger.error(f"Error deleting story {story_id}: {e}")

            return False
