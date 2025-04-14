import sqlite3
import os
import re
import random
import string
from pathlib import Path
from datetime import datetime, timedelta

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

def verify_code(slack_user_id, code):
    """Verify a code for a user and authenticate them if correct."""
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
        return False, "No verification code found"
    
    email, stored_code, created_at = result
    
    # Check if the code has expired
    if created_at < expiry_timestamp:
        conn.close()
        return False, "Verification code has expired"
    
    # Check if the code matches
    if code != stored_code:
        conn.close()
        return False, "Invalid verification code"
    
    # Code is valid, authenticate the user
    cursor.execute(
        """
        INSERT OR REPLACE INTO authenticated_users (slack_user_id, email, authenticated_at) 
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
        (slack_user_id, email)
    )
    
    # Delete the verification code
    cursor.execute(
        "DELETE FROM verification_codes WHERE slack_user_id = ?",
        (slack_user_id,)
    )
    
    conn.commit()
    conn.close()
    
    return True, email

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