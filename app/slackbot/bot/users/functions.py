import os
import requests
import xml.etree.ElementTree as ET
import traceback # For detailed error logging

# --- Helper Function for TargetProcess API Calls ---
def _query_targetprocess_user(api_url):
    """Internal helper to query TP API and parse user info."""
    tp_api_key = os.getenv("TP_API_KEY")
    if not tp_api_key:
        print("ERROR: TargetProcess API key not found in environment variables")
        return {
            "status": "failure_tool_error",
            "reason": "TargetProcess API key is missing.",
            "error_details": "TP_API_KEY not set in environment."
        }

    try:
        print(f"DEBUG: Querying TargetProcess API: {api_url}") # Log the URL being called
        response = requests.get(api_url, timeout=15)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        # Log raw response for debugging if needed
        # print(f"DEBUG: TargetProcess Raw Response: {response.text}")

        root = ET.fromstring(response.content)
        users = root.findall(".//User")

        if not users:
            print("DEBUG: No users found in TargetProcess response.")
            return None # Indicate user not found

        # --- Handling Multiple Users ---
        # If the API might return multiple users (especially for name search),
        # decide how to handle them. For now, we'll return a list.
        user_list = []
        for user_element in users:
            user_info = {}
            user_info['id'] = user_element.get('Id')
            user_info['first_name'] = user_element.findtext('FirstName')
            user_info['last_name'] = user_element.findtext('LastName')
            user_info['email'] = user_element.findtext('Email')
            # Assuming CreateDate is the anniversary date - adjust if needed
            user_info['anniversary_date'] = user_element.findtext('CreateDate')
            user_info['role'] = user_element.findtext('Role/Name')
            # Add other relevant fields if needed from TP
            user_list.append(user_info)

        print(f"DEBUG: Found {len(user_list)} user(s) in TargetProcess.")
        return user_list # Return list of found users

    except requests.exceptions.RequestException as e:
        print(f"Error calling TargetProcess API ({api_url}): {e}")
        return {
            "status": "failure_tool_error",
            "reason": "Failed to communicate with TargetProcess API.",
            "error_details": str(e)
        }
    except ET.ParseError as e:
        print(f"Error parsing TargetProcess XML response: {e}")
        # Include part of the response text if possible for context
        error_context = response.text[:500] if 'response' in locals() else "N/A"
        print(f"DEBUG: Response Text (first 500 chars): {error_context}")
        return {
            "status": "failure_tool_error",
            "reason": "Failed to parse XML response from TargetProcess API.",
            "error_details": str(e)
        }
    except Exception as e:
        print(f"Unexpected error during TargetProcess query: {e}")
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": "An unexpected error occurred during the TargetProcess query.",
            "error_details": str(e)
        }

# --- Tool Function Implementations ---

def search_user_info_by_email(email: str):
    """
    Implements the tool 'search_user_info_by_email'.
    Finds a user's details in TargetProcess based on the username part of a specific email address.
    """
    print(f"Executing search_user_info_by_email for: {email}")
    tp_api_key = os.getenv("TP_API_KEY") # Need API key for URL construction

    if not tp_api_key:
         return {
            "status": "failure_tool_error",
            "reason": "TargetProcess API key is missing.",
            "error_details": "TP_API_KEY not set in environment."
        }
    if not email:
         return {
             "status": "failure_invalid_input",
             "reason": "Email parameter cannot be empty.",
         }

    # --- Extract username part from email ---
    if '@' in email:
        username = email.split('@')[0]
        print(f"DEBUG: Extracted username '{username}' from email '{email}' for search.")
    else:
        # If no '@' is present, maybe treat the whole input as the username?
        # Or return an error? Let's assume treat as username for now.
        username = email
        print(f"DEBUG: Input '{email}' does not contain '@', searching using the full string.")
    # --- End extraction ---

    # Construct the API URL using the extracted username part with 'contains'
    # This assumes the username part is unique enough or the first result is desired.
    api_url = f"https://laneterralever.tpondemand.com/api/v1/Users?where=(Email contains '{username}')&access_token={tp_api_key}"

    result = _query_targetprocess_user(api_url)

    if isinstance(result, dict) and 'status' in result: # Check if helper returned an error dict
        return result
    elif result is None or not result: # Check if helper returned None or empty list
        print(f"User not found in TargetProcess for email containing '{username}' (from input '{email}')")
        return {
            "status": "failure_not_found",
            # Modify reason slightly to reflect the search method
            "reason": f"No user found in TargetProcess with an email containing '{username}'.",
            "email_searched": email,
            "username_searched": username
        }
    else:
        # Even searching by username might return multiple if emails share a prefix.
        # However, for this function's intent (search by *email*), we likely still
        # want the single most relevant result if possible.
        # If multiple are returned, taking the first one is a common approach.
        user_data = result[0]
        print(f"Successfully found user matching email '{email}' (searched by username '{username}'): {user_data}")
        return {
            "status": "success",
            "message": f"User found matching email '{email}'.",
            "data": user_data # Return the single user dictionary
        }

def search_user_info_by_name(first_name: str = None, last_name: str = None):
    """
    Implements the tool 'search_user_info_by_name'.
    Finds user details in TargetProcess based on first name, last name, or both.
    Can return multiple matches.
    """
    print(f"Executing search_user_info_by_name with first_name='{first_name}', last_name='{last_name}'")
    tp_api_key = os.getenv("TP_API_KEY") # Need API key for URL construction

    if not tp_api_key:
         return {
            "status": "failure_tool_error",
            "reason": "TargetProcess API key is missing.",
            "error_details": "TP_API_KEY not set in environment."
        }

    # Build the 'where' clause based on provided names
    where_clauses = []
    if first_name:
        # Using 'contains' for flexibility, adjust to 'eq' if exact match needed
        where_clauses.append(f"(FirstName contains '{first_name}')")
    if last_name:
        where_clauses.append(f"(LastName contains '{last_name}')")

    if not where_clauses:
        return {
            "status": "failure_invalid_input",
            "reason": "At least a first name or last name must be provided for the search.",
        }

    where_string = " and ".join(where_clauses) # Use AND if both are provided, OR might be too broad

    # Construct the API URL
    api_url = f"https://laneterralever.tpondemand.com/api/v1/Users?where=({where_string})&access_token={tp_api_key}"

    result = _query_targetprocess_user(api_url)

    if isinstance(result, dict) and 'status' in result: # Check if helper returned an error dict
        return result
    elif result is None or not result: # Check if helper returned None or empty list
        search_term = f"{first_name or ''} {last_name or ''}".strip()
        print(f"User not found in TargetProcess for name: {search_term}")
        return {
            "status": "failure_not_found",
            "reason": f"No user found in TargetProcess matching the name '{search_term}'.",
            "name_searched": {"first_name": first_name, "last_name": last_name}
        }
    else:
        # Return the list of users found
        search_term = f"{first_name or ''} {last_name or ''}".strip()
        print(f"Successfully found {len(result)} user(s) matching name '{search_term}': {result}")
        # Decide on message based on number of results
        if len(result) == 1:
             message = f"Found 1 user matching the name '{search_term}'."
        else:
             message = f"Found {len(result)} users matching the name '{search_term}'. You may need to clarify which user is intended."

        return {
            "status": "success",
            "message": message,
            "data": result # Return the full list of user dictionaries
        }

# --- Remove other functions not defined in tools.json ---
# Removed get_current_user_details as it wasn't in the provided tools.json 