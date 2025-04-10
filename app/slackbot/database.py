import sqlite3
import os
from pathlib import Path
from datetime import datetime, timedelta

# Ensure the database directory exists
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DB_DIR / "conversations.db"

# Define the expiry time (12 hours)
EXPIRY_HOURS = 12

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
    
    conn.commit()
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