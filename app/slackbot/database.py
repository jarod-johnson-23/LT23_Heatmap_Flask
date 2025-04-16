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
    
    conn.commit()
    conn.close()

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

    # Calculate the expiry timestamp
    expiry_timestamp = (datetime.now() - timedelta(minutes=VERIFICATION_EXPIRY_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')

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

    # Check if the code has expired
    if created_at < expiry_timestamp:
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
            INSERT INTO authenticated_users (slack_user_id, email, targetprocess_id, authenticated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(slack_user_id) DO UPDATE SET
                email=excluded.email,
                targetprocess_id=excluded.targetprocess_id,
                authenticated_at=CURRENT_TIMESTAMP
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