import os
import sqlite3
import json
import base64
import hashlib
from flask import Blueprint, request, jsonify
from cryptography.fernet import Fernet, InvalidToken
from datetime import datetime

cocopah_bp = Blueprint("cocopah_bp", __name__)

DB_DIR = os.path.join(os.path.dirname(__file__), 'db')
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "optin.db")

def generate_key(passphrase: str) -> bytes:
    """Generates a URL-safe, base64-encoded 32-byte key from a passphrase."""
    hashed_key = hashlib.sha256(passphrase.encode()).digest()
    return base64.urlsafe_b64encode(hashed_key)

def init_cocopah_db():
    """Initializes the Cocopah Opt-In SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_signups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            ip_address TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        conn.close()
        print("Cocopah Opt-In DB initialized successfully.")
    except sqlite3.Error as e:
        print(f"Error initializing Cocopah DB: {e}")

# Initialize the database when the blueprint is loaded
init_cocopah_db()

@cocopah_bp.route("/email-signup/<token>", methods=["GET"])
def cocopah_email_signup(token):
    """
    Handles email signup confirmation via a Fernet-encrypted token.
    Decrypts the token to get user info and saves it to the database.
    """
    passphrase = os.getenv("COCOPAH_PASSPHRASE")
    if not passphrase:
        print("ERROR: COCOPAH_PASSPHRASE environment variable not set.")
        return jsonify({"status": "failure", "reason": "Server configuration error."}), 500

    try:
        fernet_key = generate_key(passphrase)
        f = Fernet(fernet_key)

        # Decrypt the token using the Fernet key
        decrypted_bytes = f.decrypt(token.encode())
        decrypted_data_string = decrypted_bytes.decode()
        data = json.loads(decrypted_data_string)
        
        email = data.get("email")
        first_name = data.get("first_name")
        last_name = data.get("last_name")

        if not all([email, first_name, last_name]):
            return jsonify({"status": "failure", "reason": "Decrypted token is missing required data."}), 400

        # Get user's IP address
        if request.headers.get("X-Forwarded-For"):
            ip_address = request.headers.get("X-Forwarded-For").split(',')[0].strip()
        else:
            ip_address = request.remote_addr

        # Add data to the database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO email_signups (email, first_name, last_name, ip_address, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (email, first_name, last_name, ip_address, datetime.now())
            )
            conn.commit()
            return jsonify({
                "status": "success", 
                "message": f"Thank you, {first_name}! Your email ({email}) has been confirmed."
            }), 200
        except sqlite3.IntegrityError:
            conn.rollback()
            return jsonify({
                "status": "success",
                "message": f"Your email ({email}) has already been confirmed previously. Thank you!"
            }), 200
        finally:
            conn.close()

    except InvalidToken:
        return jsonify({"status": "failure", "reason": "The confirmation link is invalid, has been tampered with, or is not a valid token."}), 400
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        # Log the full traceback to the console for debugging
        import traceback
        traceback.print_exc()
        return jsonify({"status": "failure", "reason": "An unexpected server error occurred."}), 500 