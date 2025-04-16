import os
import requests
import xml.etree.ElementTree as ET
import traceback # For detailed error logging

# --- Helper Function for TargetProcess API Calls ---
def _query_targetprocess_user(api_url):
    """
    Internal helper to query TP API and parse user info based on the provided XML structure.
    """
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
        # Ensure the API call includes necessary fields, especially CustomFields
        # Check if the include parameter needs adjustment in the calling functions
        # Example: &include=[FirstName,LastName,Email,Id,CreateDate,Role,CustomFields[Name,Value]]
        # NOTE: The calling functions search_user_info_by_email/name currently DO NOT include CustomFields. This needs to be added there.
        response = requests.get(api_url, timeout=15)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        root = ET.fromstring(response.content)
        users = root.findall(".//User")

        if not users:
            print("DEBUG: No users found in TargetProcess response.")
            return None # Indicate user not found

        user_list = []
        for user_element in users:
            user_info = {}

            # --- Basic Fields ---
            user_info['id'] = user_element.get('Id')
            user_info['first_name'] = user_element.findtext('FirstName')
            user_info['last_name'] = user_element.findtext('LastName')
            user_info['email'] = user_element.findtext('Email')
            user_info['role'] = user_element.findtext('Role/Name')

            # --- Custom Fields ---
            # Helper to find a specific custom field value
            def get_custom_field_value(field_name):
                # Use XPath to find the Field element with the matching Name sub-element
                field = user_element.find(f".//CustomFields/Field[Name='{field_name}']")
                if field is not None:
                    value_element = field.find('Value')
                    if value_element is not None and value_element.get('nil') != 'true':
                        return value_element.text
                return None # Return None if field or value not found or is nil

            user_info['title'] = get_custom_field_value('Title')
            user_info['mobile_phone'] = get_custom_field_value('Mobile Phone')
            user_info['anniversary_date'] = get_custom_field_value('Anniversary Date') # Corrected source
            user_info['manager_name'] = get_custom_field_value('Manager Name')
            user_info['birthday'] = get_custom_field_value('Birthday Month Day')
            user_info['workstream'] = get_custom_field_value('Checkin Workstream')
            # Add any other custom fields needed here using get_custom_field_value('FieldName')
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

# IMPORTANT: Update the API URLs in the functions below to include CustomFields

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
        username = email
        print(f"DEBUG: Input '{email}' does not contain '@', searching using the full string.")
    # --- End extraction ---

    # Construct the API URL - ADD CustomFields to include parameter
    api_url = f"https://laneterralever.tpondemand.com/api/v1/Users?where=(Email contains '{username}')&access_token={tp_api_key}" # Added CustomFields

    result = _query_targetprocess_user(api_url)

    if isinstance(result, dict) and 'status' in result: # Check if helper returned an error dict
        return result
    elif result is None or not result: # Check if helper returned None or empty list
        print(f"User not found in TargetProcess for email containing '{username}' (from input '{email}')")
        return {
            "status": "failure_not_found",
            "reason": f"No user found in TargetProcess with an email containing '{username}'.",
            "email_searched": email,
            "username_searched": username
        }
    else:
        user_data = result[0]
        print(f"Successfully found user matching email '{email}' (searched by username '{username}'): {user_data}")
        return {
            "status": "success",
            "message": f"User found matching email '{email}'.",
            "data": user_data
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
    where_clauses = []
    if first_name and last_name:
        where_clauses.append(f"FullName contains '{first_name} {last_name}'")
    elif first_name:
        where_clauses.append(f"FirstName contains '{first_name}'")
    elif last_name:
        where_clauses.append(f"LastName contains '{last_name}'")

    if not where_clauses:
        return {
            "status": "failure_invalid_input",
            "reason": "At least a first name or last name must be provided for the search.",
        }

    where_string = " and ".join(where_clauses)

    # Construct the API URL - ADD CustomFields to include parameter
    api_url = f"https://laneterralever.tpondemand.com/api/v1/Users?where=({where_string})&access_token={tp_api_key}" # Added CustomFields

    result = _query_targetprocess_user(api_url)

    if isinstance(result, dict) and 'status' in result: # Check if helper returned an error dict
        print(f"DEBUG: {result}")
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
        search_term = f"{first_name or ''} {last_name or ''}".strip()
        print(f"Successfully found {len(result)} user(s) matching name '{search_term}': {result}")
        if len(result) == 1:
             message = f"Found 1 user matching the name '{search_term}'."
        else:
             message = f"Found {len(result)} users matching the name '{search_term}'. You may need to clarify which user is intended."

        return {
            "status": "success",
            "message": message,
            "data": result
        }