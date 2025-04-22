import sqlite3
import os
import functools
from app.slackbot.database import DATABASE_PATH, is_user_admin

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
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Check if the user exists
        cursor.execute(
            "SELECT slack_user_id, is_admin FROM authenticated_users WHERE email = ?", 
            (email,)
        )
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            return {
                "status": "failure_user_not_found",
                "reason": f"User with email {email} not found in the system."
            }
        
        user_slack_id, is_admin = user
        
        # Check if the user is already an admin
        if is_admin == 1:
            conn.close()
            return {
                "status": "failure_already_admin",
                "reason": f"User {email} already has admin privileges."
            }
        
        # Grant admin privileges
        cursor.execute(
            "UPDATE authenticated_users SET is_admin = 1 WHERE email = ?", 
            (email,)
        )
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "message": f"Successfully granted admin privileges to {email}.",
            "data": {
                "email": email,
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
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Check if the user exists
        cursor.execute(
            "SELECT slack_user_id, is_admin FROM authenticated_users WHERE email = ?", 
            (email,)
        )
        user = cursor.fetchone()
        
        if not user:
            conn.close()
            return {
                "status": "failure_user_not_found",
                "reason": f"User with email {email} not found in the system."
            }
        
        user_slack_id, is_admin = user
        
        # Check if the user is not an admin
        if is_admin == 0:
            conn.close()
            return {
                "status": "failure_not_admin_user",
                "reason": f"User {email} does not have admin privileges to revoke."
            }
        
        # Revoke admin privileges
        cursor.execute(
            "UPDATE authenticated_users SET is_admin = 0 WHERE email = ?", 
            (email,)
        )
        conn.commit()
        conn.close()
        
        return {
            "status": "success",
            "message": f"Successfully revoked admin privileges from {email}.",
            "data": {
                "email": email,
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
        conn = sqlite3.connect(DATABASE_PATH)
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
def check_admin_status(email, slack_id=None):
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
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        # Check if the user exists and get their admin status
        cursor.execute(
            "SELECT slack_user_id, is_admin FROM authenticated_users WHERE email = ?", 
            (email,)
        )
        user = cursor.fetchone()
        conn.close()
        
        if not user:
            return {
                "status": "failure_user_not_found",
                "reason": f"User with email {email} not found in the system."
            }
        
        user_slack_id, is_admin = user
        is_admin_bool = is_admin == 1
        
        return {
            "status": "success",
            "message": f"User {email} {'has' if is_admin_bool else 'does not have'} admin privileges.",
            "data": {
                "email": email,
                "is_admin": is_admin_bool
            }
        }
    except Exception as e:
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while checking admin status.",
            "error_details": str(e)
        }
