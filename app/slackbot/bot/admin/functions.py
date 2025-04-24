import sqlite3
import os
import functools
import re
from app.slackbot.database import DB_PATH, is_user_admin
import requests

def admin_required(func):
    """
    Decorator to check if a user has admin privileges before executing a function.
    
    This decorator wraps admin functions and performs the admin check before
    allowing the function to execute. If the user is not an admin, it returns
    a standard error response.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Extract slack_id from kwargs
        slack_id = kwargs.get('slack_id')
        
        # Check if the user is an admin
        if not is_user_admin(slack_id):
            return {
                "status": "failure_not_admin",
                "reason": "You do not have admin privileges to perform this action."
            }
        
        # If the user is an admin, execute the original function
        return func(*args, **kwargs)
    
    return wrapper

def extract_email_username(email):
    """
    Extracts the username part from an email address.
    
    Args:
        email: The email address to extract from
        
    Returns:
        The username part of the email (before the @)
    """
    if '@' in email:
        return email.split('@')[0]
    return email  # If no @ found, return the original string

def find_user_by_email_pattern(cursor, email):
    """
    Finds a user by a partial email match.
    
    Args:
        cursor: Database cursor
        email: Email or partial email to search for
        
    Returns:
        The user record if found, None otherwise
    """
    # Extract username part (before @)
    username = extract_email_username(email)
    
    # Search for the username part in the email field
    cursor.execute(
        "SELECT slack_user_id, email, is_admin FROM authenticated_users WHERE email LIKE ?", 
        (f"%{username}%",)
    )
    
    users = cursor.fetchall()
    
    if not users:
        return None
    
    if len(users) > 1:
        # If multiple matches, try to find an exact match
        for user in users:
            if user[1].lower() == email.lower():
                return user
        
        # If no exact match, return the first match
        return users[0]
    
    return users[0]  # Single match found

@admin_required
def grant_admin_privileges(email, slack_id=None):
    """
    Grants admin privileges to a user by their email address.
    Only existing admin users can grant admin privileges.
    
    Args:
        email: The email address of the user to grant admin privileges to
        slack_id: The Slack ID of the user making the request (implicitly provided)
        
    Returns:
        A dictionary containing the status and result of the operation
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Find user by email pattern
        user = find_user_by_email_pattern(cursor, email)
        
        if not user:
            conn.close()
            return {
                "status": "failure_user_not_found",
                "reason": f"No user found matching '{email}'."
            }
        
        user_slack_id, full_email, is_admin = user
        
        # Check if the user is already an admin
        if is_admin == 1:
            conn.close()
            return {
                "status": "failure_already_admin",
                "reason": f"User {full_email} already has admin privileges."
            }
        
        # Grant admin privileges
        cursor.execute(
            "UPDATE authenticated_users SET is_admin = 1 WHERE slack_user_id = ?", 
            (user_slack_id,)
        )
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "message": f"Successfully granted admin privileges to {full_email}.",
            "data": {
                "email": full_email,
                "is_admin": True
            }
        }
    except Exception as e:
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while updating admin privileges.",
            "error_details": str(e)
        }

@admin_required
def revoke_admin_privileges(email, slack_id=None):
    """
    Revokes admin privileges from a user by their email address.
    Only existing admin users can revoke admin privileges.
    
    Args:
        email: The email address of the user to revoke admin privileges from
        slack_id: The Slack ID of the user making the request (implicitly provided)
        
    Returns:
        A dictionary containing the status and result of the operation
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Find user by email pattern
        user = find_user_by_email_pattern(cursor, email)
        
        if not user:
            conn.close()
            return {
                "status": "failure_user_not_found",
                "reason": f"No user found matching '{email}'."
            }
        
        user_slack_id, full_email, is_admin = user
        
        # Check if the user is not an admin
        if is_admin == 0:
            conn.close()
            return {
                "status": "failure_not_admin_user",
                "reason": f"User {full_email} does not have admin privileges to revoke."
            }
        
        # Revoke admin privileges
        cursor.execute(
            "UPDATE authenticated_users SET is_admin = 0 WHERE slack_user_id = ?", 
            (user_slack_id,)
        )
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "message": f"Successfully revoked admin privileges from {full_email}.",
            "data": {
                "email": full_email,
                "is_admin": False
            }
        }
    except Exception as e:
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while updating admin privileges.",
            "error_details": str(e)
        }

@admin_required
def list_admin_users(slack_id=None):
    """
    Lists all users with admin privileges.
    Only existing admin users can list admin users.
    
    Args:
        slack_id: The Slack ID of the user making the request (implicitly provided)
        
    Returns:
        A dictionary containing the status and list of admin users
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Get all admin users
        cursor.execute(
            "SELECT email, slack_user_id FROM authenticated_users WHERE is_admin = 1"
        )
        admin_users = cursor.fetchall()
        conn.close()
        
        # Format the result
        admin_list = [{"email": email, "slack_id": sid} for email, sid in admin_users]
        
        return {
            "status": "success",
            "message": f"Found {len(admin_list)} users with admin privileges.",
            "data": {
                "admin_users": admin_list
            }
        }
    except Exception as e:
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while retrieving admin users.",
            "error_details": str(e)
        }

@admin_required
def check_admin_status_by_email(email, slack_id=None):
    """
    Checks if a user has admin privileges.
    Only existing admin users can check admin status.
    
    Args:
        email: The email address of the user to check admin status for
        slack_id: The Slack ID of the user making the request (implicitly provided)
        
    Returns:
        A dictionary containing the status and admin status of the specified user
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Find user by email pattern
        user = find_user_by_email_pattern(cursor, email)
        
        if not user:
            conn.close()
            return {
                "status": "failure_user_not_found",
                "reason": f"No user found matching '{email}'."
            }
        
        user_slack_id, full_email, is_admin = user
        is_admin_bool = is_admin == 1
        
        conn.close()
        
        return {
            "status": "success",
            "message": f"User {full_email} {'has' if is_admin_bool else 'does not have'} admin privileges.",
            "data": {
                "email": full_email,
                "is_admin": is_admin_bool
            }
        }
    except Exception as e:
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while checking admin status.",
            "error_details": str(e)
        }

@admin_required
def restart_opsdb(slack_id=None):
    """
    Restarts the OpsDB database and initiates a fresh data pull.
    
    Args:
        slack_id: The Slack ID of the user making the request (implicitly provided)
        
    Returns:
        A dictionary containing the status and result message
    """
    try:
        # Define the API endpoint
        api_url = "https://potenza.laneterralever.com/process-op"
        
        # Define the form data
        form_data = {
            "title": "Ops Support Tools",
            "op_name": "OpsDB Restart Tool"
        }
        
        # Make the POST request
        response = requests.post(api_url, data=form_data)
        
        # Check if the request was successful
        if response.status_code == 200:
            return {
                "status": "success",
                "message": "OpsDB restart initiated successfully. The database will refresh with fresh data shortly."
            }
        else:
            return {
                "status": "failure_tool_error",
                "reason": f"Failed to restart OpsDB. API returned status code: {response.status_code}",
                "error_details": response.text
            }
    except Exception as e:
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while attempting to restart OpsDB.",
            "error_details": str(e)
        }
