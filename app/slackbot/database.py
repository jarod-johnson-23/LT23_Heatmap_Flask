import sqlite3
import os
import re
import random
import string
from pathlib import Path
from datetime import datetime, timedelta
import requests
import xml.etree.ElementTree as ET
import traceback

# Ensure the database directory exists
DB_DIR = Path(__file__).parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DB_DIR / "conversations.db"

# Define the expiry time (12 hours)
EXPIRY_HOURS = 12
# Define the verification code expiry time (10 minutes)
VERIFICATION_EXPIRY_MINUTES = 10
# Define the acting-as expiry time (60 minutes)
ACTING_AS_EXPIRY_MINUTES = 60

def init_db():
    """Initialize the database with the required tables."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Create table for storing conversation history
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversations (
        channel_id TEXT PRIMARY KEY,
        previous_response_id TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Create table for storing authenticated users
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS authenticated_users (
        slack_user_id TEXT PRIMARY KEY,
        email TEXT UNIQUE,
        targetprocess_id INTEGER,
        authenticated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Check if is_admin column exists, add it if it doesn't
    cursor.execute("PRAGMA table_info(authenticated_users)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'is_admin' not in columns:
        print("Adding is_admin column to authenticated_users table")
        cursor.execute('''
        ALTER TABLE authenticated_users 
        ADD COLUMN is_admin INTEGER DEFAULT 0
        ''')
    
    # Create table for storing verification codes
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS verification_codes (
        slack_user_id TEXT,
        email TEXT,
        code TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (slack_user_id, email)
    )
    ''')
    
    # Create table for tracking processed messages
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS processed_messages (
        message_ts TEXT,
        channel_id TEXT,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (message_ts, channel_id)
    )
    ''')
    
    # Create table for tracking tool usage
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tool_usage_log (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        function_name TEXT NOT NULL,
        user_email TEXT NOT NULL,
        slack_id TEXT NOT NULL,
        called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Create table for tracking admins acting as other users
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS acting_as_log (
        admin_slack_id TEXT NOT NULL,
        user_slack_id TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (admin_slack_id, user_slack_id)
    )
    """)
    
    conn.commit()
    conn.close()
    
    # After initializing the database, ensure there's at least one admin
    set_first_user_as_admin()

def is_user_authenticated(slack_user_id):
    """Check if a user is authenticated."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT email FROM authenticated_users WHERE slack_user_id = ?",
        (slack_user_id,)
    )
    
    result = cursor.fetchone()
    conn.close()
    
    return result is not None

def get_user_email(slack_user_id):
    """Get the email of an authenticated user."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT email FROM authenticated_users WHERE slack_user_id = ?",
        (slack_user_id,)
    )
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result else None

def extract_email(text):
    """Extract email address from text using regex."""
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    match = re.search(email_pattern, text)
    return match.group(0) if match else None

def generate_verification_code():
    """Generate a 6-digit verification code."""
    return ''.join(random.choices(string.digits, k=6))

def store_verification_code(slack_user_id, email, code):
    """Store a verification code for a user."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute(
        """
        INSERT OR REPLACE INTO verification_codes (slack_user_id, email, code, created_at) 
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (slack_user_id, email, code)
    )
    
    conn.commit()
    conn.close()

def get_targetprocess_id(email):
    """
    Queries the TargetProcess API to find the User ID based on email.

    Args:
        email (str): The email address to search for.

    Returns:
        int: The TargetProcess User ID if found.
        None: If the user is not found, the API key is missing,
              or an error occurs during the API call or parsing.
    """
    tp_api_key = os.getenv("TP_API_KEY")
    if not tp_api_key:
        print("ERROR: TargetProcess API key not found in environment variables for ID lookup.")
        # Decide if this should raise an error or just return None.
        # Returning None allows authentication to potentially proceed without the ID.
        return None
    username = email
    if '@' in email:
      username = email.split('@')[0]

    # Use exact match for email to find the specific user
    api_url = f"https://laneterralever.tpondemand.com/api/v1/Users?where=(Email contains '{username}')&access_token={tp_api_key}" # Only need ID

    try:
        print(f"DEBUG: Querying TargetProcess for ID for email: {email}")
        response = requests.get(api_url, timeout=10) # Shorter timeout might be okay
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        root = ET.fromstring(response.content)
        user_element = root.find(".//User") # Find the first User element

        if user_element is not None:
            user_id_str = user_element.get('Id')
            if user_id_str:
                try:
                    user_id_int = int(user_id_str)
                    print(f"DEBUG: Found TargetProcess ID {user_id_int} for email {email}")
                    return user_id_int
                except ValueError:
                    print(f"ERROR: Could not convert TargetProcess ID '{user_id_str}' to integer for email {email}.")
                    return None
            else:
                print(f"DEBUG: User found for email {email}, but 'Id' attribute missing in XML.")
                return None
        else:
            print(f"DEBUG: No user found in TargetProcess for email: {email}")
            return None # User not found

    except requests.exceptions.RequestException as e:
        print(f"Error calling TargetProcess API for ID lookup ({api_url}): {e}")
        return None
    except ET.ParseError as e:
        print(f"Error parsing TargetProcess XML response for ID lookup: {e}")
        error_context = response.text[:200] if 'response' in locals() else "N/A"
        print(f"DEBUG: Response Text (first 200 chars): {error_context}")
        return None
    except Exception as e:
        print(f"Unexpected error during TargetProcess ID lookup for {email}: {e}")
        traceback.print_exc()
        return None

def verify_code(slack_user_id, code):
    """
    Verify a code for a user. If correct, attempt to find their TargetProcess ID.
    Authenticate the user only if the code is valid AND the TargetProcess ID is found.
    """
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT email, code, created_at FROM verification_codes
        WHERE slack_user_id = ?
        """,
        (slack_user_id,)
    )

    result = cursor.fetchone()

    if not result:
        conn.close()
        return False, "No verification code found for this user."

    email, stored_code, created_at = result

    # Parse the created_at timestamp and check if it has expired
    created_at_datetime = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
    current_datetime = datetime.now()
    
    # Check if the code has expired (current time > created_at + expiry minutes)
    if current_datetime > (created_at_datetime + timedelta(minutes=VERIFICATION_EXPIRY_MINUTES)):
        # Clean up expired code
        cursor.execute("DELETE FROM verification_codes WHERE slack_user_id = ?", (slack_user_id,))
        conn.commit()
        conn.close()
        return False, "Verification code has expired. Please request a new one."

    # Check if the code matches
    if code != stored_code:
        # Don't delete the code yet, maybe they mistyped
        conn.close()
        return False, "Invalid verification code."

    # --- Code is valid, now attempt to get the targetprocess_id ---
    print(f"Verification code matched for {email}. Attempting to get TargetProcess ID.")
    targetprocess_id = get_targetprocess_id(email)

    # --- CRITICAL CHECK: Fail authentication if TP ID not found ---
    if targetprocess_id is None:
        print(f"ERROR: Could not retrieve TargetProcess ID for {email}. Authentication failed.")
        # Clean up the used verification code even on failure
        cursor.execute("DELETE FROM verification_codes WHERE slack_user_id = ?", (slack_user_id,))
        conn.commit()
        conn.close()
        # Return a specific error message to the user
        return False, "Your code is correct, but could not find a matching TargetProcess user for your email. Please ensure your email matches TargetProcess exactly. Authentication failed."
    # --- End TP ID check ---

    # --- Both code and TP ID are valid, authenticate the user ---
    try:
        print(f"TargetProcess ID {targetprocess_id} found for {email}. Authenticating user.")
        cursor.execute(
            """
            INSERT INTO authenticated_users (slack_user_id, email, targetprocess_id, authenticated_at, is_admin)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, 0)
            ON CONFLICT(slack_user_id) DO UPDATE SET
                email=excluded.email,
                targetprocess_id=excluded.targetprocess_id,
                authenticated_at=CURRENT_TIMESTAMP,
                is_admin=excluded.is_admin
            """,
            (slack_user_id, email, targetprocess_id) # Pass the retrieved ID
        )

        # Code verified, TP ID found, user inserted/updated. Now delete the verification code.
        cursor.execute(
            "DELETE FROM verification_codes WHERE slack_user_id = ?",
            (slack_user_id,)
        )

        conn.commit()
        print(f"User {slack_user_id} ({email}) authenticated successfully. TP ID: {targetprocess_id}")
        return True, email

    except sqlite3.Error as e:
        print(f"Database error during user authentication or code deletion for {slack_user_id}: {e}")
        conn.rollback() # Roll back any partial changes
        # Clean up the verification code even on database error during insert
        try:
            cursor.execute("DELETE FROM verification_codes WHERE slack_user_id = ?", (slack_user_id,))
            conn.commit()
        except sqlite3.Error as del_e:
             print(f"Failed to delete verification code after DB error for {slack_user_id}: {del_e}")
        return False, "A database error occurred during authentication."
    finally:
        conn.close()

def get_previous_response_id(channel_id):
    """Get the previous response ID for a given channel if not expired."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Calculate the expiry timestamp (current time - 12 hours)
    expiry_timestamp = (datetime.now() - timedelta(hours=EXPIRY_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute(
        """
        SELECT previous_response_id, last_updated FROM conversations 
        WHERE channel_id = ?
        """, 
        (channel_id,)
    )
    
    result = cursor.fetchone()
    
    if result:
        previous_id, timestamp = result
        
        # Check if the conversation has expired
        if timestamp < expiry_timestamp:
            # Conversation expired, remove the previous_response_id
            cursor.execute(
                "UPDATE conversations SET previous_response_id = NULL WHERE channel_id = ?",
                (channel_id,)
            )
            conn.commit()
            conn.close()
            return None
        else:
            conn.close()
            return previous_id
    else:
        conn.close()
        return None

def update_response_id(channel_id, response_id):
    """Update or insert the response ID for a channel."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute(
        """
        INSERT INTO conversations (channel_id, previous_response_id, last_updated) 
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(channel_id) 
        DO UPDATE SET 
            previous_response_id = ?,
            last_updated = CURRENT_TIMESTAMP
        """, 
        (channel_id, response_id, response_id)
    )
    
    conn.commit()
    conn.close()

def cleanup_expired_conversations():
    """Remove expired conversation records from the database."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Calculate the expiry timestamp (current time - 12 hours)
    expiry_timestamp = (datetime.now() - timedelta(hours=EXPIRY_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
    
    # Delete expired conversations
    cursor.execute(
        "DELETE FROM conversations WHERE last_updated < ?",
        (expiry_timestamp,)
    )
    
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    return deleted_count 

def reset_all_conversations():
    """Reset all conversations by setting previous_response_id to NULL."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    cursor.execute("UPDATE conversations SET previous_response_id = NULL")
    
    reset_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    return reset_count 

def is_message_processed(message_ts, channel_id):
    """Check if a message has already been processed to prevent duplicate responses."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Create the processed_messages table if it doesn't exist
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS processed_messages (
        message_ts TEXT,
        channel_id TEXT,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (message_ts, channel_id)
    )
    ''')
    
    # Check if this message has been processed
    cursor.execute(
        "SELECT 1 FROM processed_messages WHERE message_ts = ? AND channel_id = ?",
        (message_ts, channel_id)
    )
    
    result = cursor.fetchone() is not None
    conn.close()
    
    return result

def mark_message_processed(message_ts, channel_id):
    """Mark a message as processed to prevent duplicate responses."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Ensure the table exists
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS processed_messages (
        message_ts TEXT,
        channel_id TEXT,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (message_ts, channel_id)
    )
    ''')
    
    # Insert the message as processed
    cursor.execute(
        "INSERT OR IGNORE INTO processed_messages (message_ts, channel_id) VALUES (?, ?)",
        (message_ts, channel_id)
    )
    
    conn.commit()
    conn.close()

def cleanup_old_processed_messages():
    """Remove processed message records older than 24 hours."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Calculate the expiry timestamp (current time - 24 hours)
    expiry_timestamp = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    
    # Delete old processed messages
    cursor.execute(
        "DELETE FROM processed_messages WHERE processed_at < ?",
        (expiry_timestamp,)
    )
    
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    return deleted_count 

def get_targetprocess_id_by_slack_id(slack_user_id):
    """Retrieves the targetprocess_id for a given Slack user ID."""
    conn = None
    try:
        # Use the existing DB_PATH or define it if not globally available here
        # Assuming DB_PATH is defined globally or accessible
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT targetprocess_id FROM authenticated_users WHERE slack_user_id = ?",
            (slack_user_id,)
        )
        result = cursor.fetchone()
        if result and result[0] is not None:
            print(f"DEBUG: Found targetprocess_id {result[0]} for slack_id {slack_user_id}")
            return result[0]
        else:
            print(f"DEBUG: No targetprocess_id found in DB for slack_id {slack_user_id}")
            return None
    except sqlite3.Error as e:
        print(f"Database error fetching targetprocess_id for {slack_user_id}: {e}")
        return None
    finally:
        if conn:
            conn.close() 

def log_tool_usage(function_name: str, user_email: str, slack_id: str):
    """Logs the usage of a tool function."""
    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tool_usage_log (function_name, user_email, slack_id)
            VALUES (?, ?, ?)
            """,
            (function_name, user_email, slack_id)
        )
        conn.commit()
        print(f"DEBUG: Logged tool usage: Function='{function_name}', User='{user_email}', SlackID='{slack_id}'")
    except sqlite3.Error as e:
        print(f"ERROR: Failed to log tool usage for function '{function_name}', user '{user_email}': {e}")
        # Decide if you want to rollback? Probably not critical enough to stop execution.
        # if conn:
        #     conn.rollback()
    except Exception as e:
        # Catch other potential errors
        print(f"ERROR: An unexpected error occurred during tool usage logging: {e}")
    finally:
        if conn:
            conn.close() 

def is_user_admin(slack_id):
    """
    Check if a user has admin privileges.
    
    Args:
        slack_id: The Slack ID of the user to check
        
    Returns:
        bool: True if the user is an admin, False otherwise
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Query the is_admin column for the given slack_id
        cursor.execute(
            "SELECT is_admin FROM authenticated_users WHERE slack_user_id = ?", 
            (slack_id,)
        )
        result = cursor.fetchone()
        
        conn.close()
        
        # Return True if the user exists and is_admin is 1, False otherwise
        return result is not None and result[0] == 1
    except Exception as e:
        print(f"Error checking admin status for user {slack_id}: {e}")
        return False 

def set_first_user_as_admin():
    """
    Sets the first user in the system as an admin if no admins exist.
    This ensures there's always at least one admin in the system.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Check if any admins exist
        cursor.execute("SELECT COUNT(*) FROM authenticated_users WHERE is_admin = 1")
        admin_count = cursor.fetchone()[0]
        
        if admin_count == 0:
            # No admins exist, set the first user as admin
            cursor.execute(
                "UPDATE authenticated_users SET is_admin = 1 WHERE targetprocess_id = 543"
            )
            
            # Get the email of the user who was made admin
            cursor.execute(
                "SELECT email FROM authenticated_users WHERE is_admin = 1 LIMIT 1"
            )
            admin_email = cursor.fetchone()
            
            if admin_email:
                print(f"Set first user {admin_email[0]} as admin")
            
            conn.commit()
        
        conn.close()
    except Exception as e:
        print(f"Error setting first user as admin: {e}") 

def cleanup_expired_acting_as_logs():
    """Remove acting_as_log entries older than the configured expiry window."""
    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        expiry_timestamp = (datetime.now() - timedelta(minutes=ACTING_AS_EXPIRY_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            "DELETE FROM acting_as_log WHERE created_at < ?",
            (expiry_timestamp,)
        )
        deleted_count = cursor.rowcount
        conn.commit()
        return deleted_count
    except sqlite3.Error as e:
        print(f"ERROR: Failed to cleanup expired acting_as_log entries: {e}")
        if conn:
            conn.rollback()
        return 0
    finally:
        if conn:
            conn.close()

def add_acting_as_log(admin_slack_id: str, user_slack_id: str):
    """Adds or updates an entry in the acting_as_log table."""
    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Delete any existing entry for this admin
        cursor.execute(
            "DELETE FROM acting_as_log WHERE admin_slack_id = ?",
            (admin_slack_id,)
        )
        
        # Best-effort cleanup of any expired entries before inserting a new one
        try:
            expiry_timestamp = (datetime.now() - timedelta(minutes=ACTING_AS_EXPIRY_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute(
                "DELETE FROM acting_as_log WHERE created_at < ?",
                (expiry_timestamp,)
            )
        except Exception:
            # Non-fatal; continue with insert
            pass

        # Insert the new entry
        cursor.execute(
            """
            INSERT INTO acting_as_log (admin_slack_id, user_slack_id, created_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (admin_slack_id, user_slack_id)
        )
        conn.commit()
        print(f"DEBUG: Added acting_as_log: Admin='{admin_slack_id}', User='{user_slack_id}'")
        return True, None
    except sqlite3.Error as e:
        print(f"ERROR: Failed to add acting_as_log for admin '{admin_slack_id}', user '{user_slack_id}': {e}")
        if conn:
            conn.rollback()
        return False, str(e)
    except Exception as e:
        print(f"ERROR: An unexpected error occurred during add_acting_as_log: {e}")
        if conn:
            conn.rollback()
        return False, str(e)
    finally:
        if conn:
            conn.close()

def delete_acting_as_log(admin_slack_id: str):
    """Deletes an entry from the acting_as_log table for a given admin."""
    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM acting_as_log WHERE admin_slack_id = ?",
            (admin_slack_id,)
        )
        deleted_count = cursor.rowcount
        conn.commit()
        if deleted_count > 0:
            print(f"DEBUG: Deleted acting_as_log for admin '{admin_slack_id}'")
            return True, None
        else:
            print(f"DEBUG: No acting_as_log entry found to delete for admin '{admin_slack_id}'")
            return False, "No active 'acting as' session found for this admin."
    except sqlite3.Error as e:
        print(f"ERROR: Failed to delete acting_as_log for admin '{admin_slack_id}': {e}")
        if conn:
            conn.rollback()
        return False, str(e)
    except Exception as e:
        print(f"ERROR: An unexpected error occurred during delete_acting_as_log: {e}")
        if conn:
            conn.rollback()
        return False, str(e)
    finally:
        if conn:
            conn.close() 

def get_acting_as_user_id(admin_slack_id: str):
    """Retrieves the user_slack_id an admin is currently acting as."""
    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        # Remove any expired entry for this admin
        expiry_timestamp = (datetime.now() - timedelta(minutes=ACTING_AS_EXPIRY_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            "DELETE FROM acting_as_log WHERE admin_slack_id = ? AND created_at < ?",
            (admin_slack_id, expiry_timestamp)
        )
        if cursor.rowcount > 0:
            conn.commit()

        # Fetch active (non-expired) acting-as session
        cursor.execute(
            "SELECT user_slack_id FROM acting_as_log WHERE admin_slack_id = ? AND created_at >= ?",
            (admin_slack_id, expiry_timestamp)
        )
        result = cursor.fetchone()
        if result:
            print(f"DEBUG: Admin '{admin_slack_id}' is acting as user '{result[0]}'")
            return result[0]
        else:
            print(f"DEBUG: Admin '{admin_slack_id}' is not currently acting as any user.")
            return None
    except sqlite3.Error as e:
        print(f"ERROR: Database error fetching acting_as_user_id for admin '{admin_slack_id}': {e}")
        return None
    except Exception as e:
        print(f"ERROR: An unexpected error occurred during get_acting_as_user_id for admin '{admin_slack_id}': {e}")
        return None
    finally:
        if conn:
            conn.close() 

def find_user_by_name_parts(first_name: str = None, last_name: str = None):
    """Finds a user in authenticated_users by first and/or last name from their email."""
    if not first_name and not last_name:
        return None, "Either first name or last name must be provided."

    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()

        query_parts = []
        params = []

        if first_name and last_name:
            # Search for firstname.lastname@
            pattern = f"{first_name.lower()}.{last_name.lower()}@%"
            query_parts.append("LOWER(email) LIKE ?")
            params.append(pattern)
        elif first_name:
            # Search for firstname.%@
            pattern = f"{first_name.lower()}.%@%"
            query_parts.append("LOWER(email) LIKE ?")
            params.append(pattern)
        elif last_name:
            # Search for %.lastname@
            pattern = f"%.{last_name.lower()}@%"
            query_parts.append("LOWER(email) LIKE ?")
            params.append(pattern)

        sql_query = f"SELECT slack_user_id, email, targetprocess_id FROM authenticated_users WHERE {' AND '.join(query_parts)}"
        
        cursor.execute(sql_query, tuple(params))
        result = cursor.fetchone()

        if result:
            # result will be a tuple (slack_user_id, email, targetprocess_id)
            print(f"DEBUG: Found user by name parts ({first_name}, {last_name}): {result}")
            return result, None
        else:
            print(f"DEBUG: No user found by name parts ({first_name}, {last_name})")
            return None, "No authenticated user was found with the given name(s)."

    except sqlite3.Error as e:
        print(f"ERROR: Database error in find_user_by_name_parts for ({first_name}, {last_name}): {e}")
        return None, f"A database error occurred: {e}"
    except Exception as e:
        print(f"ERROR: Unexpected error in find_user_by_name_parts for ({first_name}, {last_name}): {e}")
        return None, f"An unexpected error occurred: {e}"
    finally:
        if conn:
            conn.close()
