import os
import sqlite3
from flask import Blueprint, request, jsonify, current_app
from itsdangerous import SignatureExpired, BadTimeSignature
from datetime import datetime

cocopah_bp = Blueprint("cocopah_bp", __name__)

DB_DIR = os.path.join(os.path.dirname(__file__), 'db')
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "optin.db")

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
    Handles email signup confirmation via a tokenized URL.
    Decrypts the token to get user info and saves it to the database.
    """
    serializer = current_app.serializer
    try:
        # Decrypt the token, max_age can be set if tokens should expire
        data = serializer.loads(token, max_age=None) 
        email = data.get("email")
        first_name = data.get("first_name")
        last_name = data.get("last_name")

        if not all([email, first_name, last_name]):
            return jsonify({"status": "failure", "reason": "Token is missing required data."}), 400

        # Get user's IP address. Prioritize X-Forwarded-For header to get the
        # original client IP even when behind a proxy (like the WordPress site).
        if request.headers.get("X-Forwarded-For"):
            # The header can be a comma-separated list of IPs. The first one is the client.
            ip_address = request.headers.get("X-Forwarded-For").split(',')[0].strip()
        else:
            # Fallback to the direct IP of the requestor.
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
            # This happens if the email is already in the database (UNIQUE constraint)
            conn.rollback()
            return jsonify({
                "status": "success",
                "message": f"Your email ({email}) has already been confirmed previously. Thank you!"
            }), 200
        finally:
            conn.close()

    except SignatureExpired:
        return jsonify({"status": "failure", "reason": "The confirmation link has expired."}), 400
    except BadTimeSignature:
        return jsonify({"status": "failure", "reason": "The confirmation link is invalid or has been tampered with."}), 400
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return jsonify({"status": "failure", "reason": "An unexpected error occurred."}), 500 