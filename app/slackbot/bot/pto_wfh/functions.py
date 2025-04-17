import os
import json
import traceback
from datetime import datetime, date, timezone
from app.slackbot.database import get_targetprocess_id_by_slack_id # Import the new helper
from app.slackbot.potenza import potenza_api # Import the Potenza API instance
import re
import requests
import logging
import sqlite3
from typing import Union, Optional

# Configure logging if not already done in this module scope
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Database Helper ---
# Assume your DB is in the project root or adjust path as needed
DATABASE_PATH = os.path.join(os.path.dirname(__file__), '..', 'data/conversations.db') # Adjust path if needed

# --- Helper Function for Parsing TargetProcess Date Strings ---
def parse_tp_date(tp_date_str: Optional[str]) -> Optional[date]:
    """
    Parses TargetProcess's /Date(milliseconds-offset)/ format into a date object.
    Returns None if parsing fails or input is None.
    """
    if not tp_date_str:
        return None
    match = re.match(r"/Date\((\d+)(?:[+-]\d+)?\)/", tp_date_str)
    if match:
        milliseconds = int(match.group(1))
        try:
            # Convert milliseconds to seconds and create a datetime object in UTC
            dt_utc = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
            # Return just the date part
            return dt_utc.date()
        except (ValueError, OSError) as e:
            logging.error(f"Error converting timestamp {milliseconds} to date: {e}")
            return None
    else:
        logging.warning(f"Could not parse TargetProcess date string: {tp_date_str}")
        return None

# --- PTO Balance Function ---
def get_pto_balance(slack_id: str):
    """
    Retrieves the current PTO balance details for the user associated with the given slack_id.

    Args:
        slack_id: The Slack ID of the user requesting their PTO balance.

    Returns:
        A dictionary containing the status and PTO balance details or an error message.
    """
    print(f"Executing get_pto_balance for slack_id: {slack_id}")

    # 1. Get TargetProcess ID from SQLite DB using slack_id
    targetprocess_id = get_targetprocess_id_by_slack_id(slack_id)

    if targetprocess_id is None:
        print(f"ERROR: Could not find targetprocess_id for slack_id {slack_id} in local database.")
        # Use failure_user_not_linked for consistency with other functions
        return {
            "status": "failure_user_not_linked",
            "reason": "Could not find your TargetProcess ID link in my records. Have you authenticated successfully?",
            "slack_id": slack_id
        }
    print(f"DEBUG: Found TargetProcess ID {targetprocess_id} for Slack ID {slack_id}") # Added debug print

    # 2. Define and format the SQL query
    sql_query = f"""
        SELECT pto_hours_logged, allotted_pto
             , COALESCE(pto_rollover_hours,0) as rollover
             , (SELECT COALESCE(sum(hours),0)
                FROM recent_actual_hours_accreted
                WHERE user_id = {targetprocess_id}
                    AND is_pto = 1
                    AND serial_day > 0
                    AND transaction_date like (select substr(day,1,4) || '%' FROM today)
               ) as upcoming_pto_hours
        FROM recent_annual_pto_figures
        WHERE for_year_of = (select CAST(substr(day,1,4) as int) from today)
            AND user_id = {targetprocess_id}
    """
    # 3. Execute the query using Potenza API
    try:
        print(f"DEBUG: Executing Potenza SQL for TP User ID {targetprocess_id}:\n{sql_query}")
        result = potenza_api.execute_sql(sql_query)
        print(f"DEBUG: Potenza Result: {result}")

        # 4. Process the result
        if result and isinstance(result, list) and len(result) > 0:
            # Assuming the query returns one row with the needed columns
            data = result[0]
            logged = float(data.get('pto_hours_logged', 0))
            allotted = float(data.get('allotted_pto', 0))
            rollover = float(data.get('rollover', 0))
            upcoming = float(data.get('upcoming_pto_hours', 0)) # Use the alias from the query

            # Calculate remaining PTO
            # Remaining = (Starting Allotment + Rollover) - Logged Hours - Upcoming Hours
            remaining = (allotted + rollover) - logged - upcoming

            print(f"DEBUG: PTO Calculation: ({allotted} + {rollover}) - {logged} - {upcoming} = {remaining}")

            # 5. Format success response
            return {
                "status": "success",
                "message": f"Successfully retrieved PTO balance for user {targetprocess_id}.",
                "data": {
                    "allotted_pto_hours": allotted,
                    "rollover_hours": rollover,
                    "logged_pto_hours": logged,
                    "upcoming_pto_hours": upcoming,
                    "remaining_pto_hours": round(remaining, 2) # Round to 2 decimal places
                }
            }
        elif isinstance(result, list) and len(result) == 0:
             print(f"WARNING: Potenza query returned no rows for TP User ID {targetprocess_id}.")
             # Use failure_no_data_found for consistency
             return {
                 "status": "failure_no_data_found",
                 "reason": f"Could not find PTO balance data for the current year for TargetProcess user {targetprocess_id}.",
                 "targetprocess_id": targetprocess_id
             }
        else:
            # Handle unexpected result format from Potenza
            print(f"ERROR: Unexpected result format from Potenza API for TP User ID {targetprocess_id}: {result}")
            return {
                "status": "failure_tool_error",
                "reason": "Received an unexpected response format while fetching PTO data.",
                "error_details": f"Unexpected Potenza result type: {type(result)}"
            }

    except Exception as e:
        print(f"ERROR: Failed to execute Potenza SQL or process result for TP User ID {targetprocess_id}: {e}")
        traceback.print_exc() # Make sure traceback is imported
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source.",
            "error_details": str(e)
        }

# --- Functions to Get Users on PTO/WFH Today ---
def get_users_on_pto_today():
    """
    Retrieves a list of users who have logged PTO for today.

    Returns:
        A dictionary containing the status and a list of users on PTO today,
        including their name and whether it's a partial day, or an error message.
    """
    print("Executing get_users_on_pto_today")

    # 1. Get today's date
    today_date = datetime.now().strftime('%Y-%m-%d')
    print(f"DEBUG: Checking PTO for date: {today_date}")

    # 2. Define and format the SQL query
    #    Note: Ensure the date is quoted in the SQL WHERE clause.
    sql_query = f"""
        SELECT u.distinct_name, case when hours < 7 THEN 1 else 0 end as is_partial
        FROM actual_hours_recent_detailed ah left join users u
             on u.user_id = ah.user_id
        WHERE is_pto = 1
             AND u.is_active = 1
            AND transaction_date = '{today_date}'
        ORDER BY ah.role_type, u.distinct_name
    """

    # 3. Execute the query using Potenza API
    try:
        print(f"DEBUG: Executing Potenza SQL for users on PTO today:\n{sql_query}")
        result = potenza_api.execute_sql(sql_query)
        print(f"DEBUG: Potenza Result: {result}")

        # 4. Process the result
        if result and isinstance(result, list):
            users_on_pto = []
            for row in result:
                users_on_pto.append({
                    "name": row.get('distinct_name'),
                    "is_partial_day": bool(row.get('is_partial', 0)) # Convert 1/0 to True/False
                })

            message = f"Found {len(users_on_pto)} user(s) on PTO today ({today_date})."
            if not users_on_pto:
                 message = f"No users found on PTO today ({today_date})."

            print(f"DEBUG: {message}")

            # 5. Format success response
            return {
                "status": "success",
                "message": message,
                "data": {
                    "date_checked": today_date,
                    "users_on_pto": users_on_pto
                }
            }
        elif isinstance(result, list) and len(result) == 0:
             # This is also a valid success case (no one is out)
             message = f"No users found on PTO today ({today_date})."
             print(f"DEBUG: {message}")
             return {
                 "status": "success",
                 "message": message,
                 "data": {
                     "date_checked": today_date,
                     "users_on_pto": []
                 }
             }
        else:
            # Handle unexpected result format from Potenza
            print(f"ERROR: Unexpected result format from Potenza API for users on PTO: {result}")
            return {
                "status": "failure_tool_error",
                "reason": "Received an unexpected response format while fetching today's PTO list.",
                "error_details": f"Unexpected Potenza result type: {type(result)}"
            }
    except Exception as e:
        print(f"ERROR: Failed to execute Potenza SQL or process result for users on PTO today: {e}")
        traceback.print_exc() # Ensure traceback is imported
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for today's PTO list.",
            "error_details": str(e)
        }

def get_users_wfh_today():
    """
    Retrieves a list of users who are marked as Working From Home (WFH) today.

    Returns:
        A dictionary containing the status and a list of users WFH today,
        including their name and whether it's a partial day, or an error message.
    """
    print("Executing get_users_wfh_today")

    # 1. Get today's date
    today_date = datetime.now().strftime('%Y-%m-%d')
    print(f"DEBUG: Checking WFH for date: {today_date}")

    # 2. Define and format the SQL query
    #    Note: Ensure the date is quoted in the SQL WHERE clause.
    sql_query = f"""
        SELECT u.distinct_name, case when hours < 7 THEN 1 else 0 end as is_partial
        FROM actual_hours_recent_detailed ah left join users u
             on u.user_id = ah.user_id
        WHERE ah.story_id in ( SELECT story_id FROM recent_wfh_stories )
             AND u.is_active = 1
             AND transaction_date = '{today_date}'
        ORDER BY ah.role_type, u.distinct_name
    """

    # 3. Execute the query using Potenza API
    try:
        print(f"DEBUG: Executing Potenza SQL for users WFH today:\n{sql_query}")
        result = potenza_api.execute_sql(sql_query)
        print(f"DEBUG: Potenza Result: {result}")

        # 4. Process the result
        if isinstance(result, list):
            users_wfh = []
            for row in result:
                users_wfh.append({
                    "name": row.get('distinct_name'),
                    "is_partial_day": bool(row.get('is_partial', 0)) # Convert 1/0 to True/False
                })

            message = f"Found {len(users_wfh)} user(s) working from home today ({today_date})."
            if not users_wfh:
                 message = f"No users found working from home today ({today_date})."

            print(f"DEBUG: {message}")

            # 5. Format success response
            return {
                "status": "success",
                "message": message,
                "data": {
                    "date_checked": today_date,
                    "users_wfh": users_wfh
                }
            }
        # Handle empty list as success (no one WFH) - combined with above check
        # elif isinstance(result, list) and len(result) == 0: ...

        else:
            # Handle unexpected result format from Potenza
            print(f"ERROR: Unexpected result format from Potenza API for users WFH: {result}")
            return {
                "status": "failure_tool_error",
                "reason": "Received an unexpected response format while fetching today's WFH list.",
                "error_details": f"Unexpected Potenza result type: {type(result)}"
            }

    except Exception as e:
        print(f"ERROR: Failed to execute Potenza SQL or process result for users WFH today: {e}")
        traceback.print_exc() # Ensure traceback is imported
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for today's WFH list.",
            "error_details": str(e)
        }


# --- Functions to Get Upcoming PTO/WFH by Name ---
def get_upcoming_pto_by_name(name: str):
    """
    Retrieves upcoming PTO dates for a user based on their name.

    Args:
        name: The name (or partial name) of the user to search for.

    Returns:
        A dictionary containing the status and a list of upcoming PTO entries
        (each with transaction_date and day_position), or an error message.
    """
    print(f"Executing get_upcoming_pto_by_name for name: '{name}'")

    # 1. Validate input (optional but good practice)
    if not name or not isinstance(name, str) or len(name.strip()) == 0:
        return {
            "status": "failure_invalid_input",
            "reason": "A valid name must be provided to search for upcoming PTO.",
            "missing_parameters": ["name"]
        }

    # Sanitize name slightly - basic example, more robust needed for production
    # to prevent SQL injection if not handled by the API layer.
    # Assuming potenza_api.execute_sql handles basic sanitization or uses parameterized queries.
    # If not, more care is needed here. For now, just strip whitespace.
    search_name = name.strip()

    # 2. Define and format the SQL query
    #    Using LIKE for partial matching as requested.
    sql_query = f"""
        SELECT transaction_date, day_position
        FROM pto_runs
        WHERE person LIKE '%{search_name}%'
        ORDER BY transaction_date
    """

    # 3. Execute the query using Potenza API
    try:
        print(f"DEBUG: Executing Potenza SQL for upcoming PTO for name '{search_name}':\n{sql_query}")
        result = potenza_api.execute_sql(sql_query)
        print(f"DEBUG: Potenza Result: {result}")

        # 4. Process the result
        if isinstance(result, list):
            # Process the list, even if empty
            pto_entries = []
            for row in result:
                # Ensure expected keys exist, handle potential None values if necessary
                entry = {
                    "transaction_date": row.get('transaction_date'),
                    "day_position": row.get('day_position')
                }
                # Add validation if needed (e.g., check if date is valid format)
                if entry["transaction_date"] and entry["day_position"] is not None:
                     pto_entries.append(entry)
                else:
                     print(f"WARNING: Skipping row with missing data: {row}")


            message = f"Found {len(pto_entries)} upcoming PTO entries matching the name '{search_name}'."
            if not pto_entries:
                 # Use failure_no_data_found status if nothing found, for consistency
                 message = f"No upcoming PTO entries found matching the name '{search_name}'."
                 print(f"DEBUG: {message}")
                 return {
                     "status": "failure_no_data_found",
                     "reason": message,
                     "data": {
                         "search_name": search_name,
                         "upcoming_pto": []
                     }
                 }

            print(f"DEBUG: {message}")

            # 5. Format success response
            return {
                "status": "success",
                "message": message,
                "data": {
                    "search_name": search_name,
                    "upcoming_pto": pto_entries # Return the list of entries
                }
            }
        else:
            # Handle unexpected result format from Potenza
            print(f"ERROR: Unexpected result format from Potenza API for upcoming PTO: {result}")
            return {
                "status": "failure_tool_error",
                "reason": "Received an unexpected response format while fetching upcoming PTO.",
                "error_details": f"Unexpected Potenza result type: {type(result)}"
            }

    except Exception as e:
        print(f"ERROR: Failed to execute Potenza SQL or process result for upcoming PTO for name '{search_name}': {e}")
        traceback.print_exc() # Ensure traceback is imported
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for upcoming PTO.",
            "error_details": str(e)
        }


def get_upcoming_wfh_by_name(name: str):
    """
    Retrieves upcoming Work From Home (WFH) dates for a user based on their name.

    Args:
        name: The name (or partial name) of the user to search for.

    Returns:
        A dictionary containing the status and a list of upcoming WFH entries
        (each with transaction_date, hours, is_partial_day), or an error message.
    """
    print(f"Executing get_upcoming_wfh_by_name for name: '{name}'")

    # 1. Validate input
    if not name or not isinstance(name, str) or len(name.strip()) == 0:
        return {
            "status": "failure_invalid_input",
            "reason": "A valid name must be provided to search for upcoming WFH.",
            "missing_parameters": ["name"]
        }

    search_name = name.strip()

    # 2. Define and format the SQL query
    #    Added ORDER BY transaction_date for clarity.
    sql_query = f"""
        SELECT transaction_date, hours, case when hours < 7 THEN 1 else 0 end as is_partial
        FROM actual_hours_recent_detailed ah
        WHERE ah.story_id in ( SELECT story_id FROM recent_wfh_stories )
          AND person LIKE '%{search_name}%'
          AND serial_day >= 0
        ORDER BY transaction_date
    """

    # 3. Execute the query using Potenza API
    try:
        print(f"DEBUG: Executing Potenza SQL for upcoming WFH for name '{search_name}':\n{sql_query}")
        result = potenza_api.execute_sql(sql_query)
        print(f"DEBUG: Potenza Result: {result}")

        # 4. Process the result
        if isinstance(result, list):
            wfh_entries = []
            for row in result:
                # Extract relevant data
                entry = {
                    "transaction_date": row.get('transaction_date'),
                    "hours": row.get('hours'),
                    "is_partial_day": bool(row.get('is_partial', 0))
                }
                # Basic validation
                if entry["transaction_date"] and entry["hours"] is not None:
                     wfh_entries.append(entry)
                else:
                     print(f"WARNING: Skipping row with missing data in upcoming WFH query: {row}")

            message = f"Found {len(wfh_entries)} upcoming WFH entries matching the name '{search_name}'."
            if not wfh_entries:
                 # Use failure_no_data_found status if nothing found
                 message = f"No upcoming WFH entries found matching the name '{search_name}'."
                 print(f"DEBUG: {message}")
                 return {
                     "status": "failure_no_data_found",
                     "reason": message,
                     "data": {
                         "search_name": search_name,
                         "upcoming_wfh": []
                     }
                 }

            print(f"DEBUG: {message}")

            # 5. Format success response
            return {
                "status": "success",
                "message": message,
                "data": {
                    "search_name": search_name,
                    "upcoming_wfh": wfh_entries # Return the list of entries
                }
            }
        else:
            # Handle unexpected result format from Potenza
            print(f"ERROR: Unexpected result format from Potenza API for upcoming WFH: {result}")
            return {
                "status": "failure_tool_error",
                "reason": "Received an unexpected response format while fetching upcoming WFH.",
                "error_details": f"Unexpected Potenza result type: {type(result)}"
            }
    except Exception as e:
        print(f"ERROR: Failed to execute Potenza SQL or process result for upcoming WFH for name '{search_name}': {e}")
        traceback.print_exc() # Ensure traceback is imported
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for upcoming WFH.",
            "error_details": str(e)
        }


# --- Consolidated Logging Function ---
def log_time_entry(slack_id: str, time_type: str, entries: list):
    """
    Logs time entries (PTO, WFH, Sick) in TargetProcess for the requesting user.

    Args:
        slack_id: The Slack ID of the user logging time.
        time_type: The type of time entry ("PTO", "WFH", "Sick").
        entries: A list of dictionaries, each representing a single day's entry.
                 Each dict must contain 'date' (YYYY-MM-DD) and optionally 'hours' (int, defaults to 8).

    Returns:
        A dictionary containing the overall status and results for each logging attempt.
    """
    print(f"Executing log_time_entry for Slack ID: {slack_id}, Type: {time_type}, Entries: {entries}")

    # --- Input Validation ---
    if not slack_id or not isinstance(slack_id, str):
        return {"status": "failure_invalid_input", "reason": "Invalid Slack ID provided internally."}
    if time_type not in ["PTO", "WFH", "Sick"]:
        return {"status": "failure_invalid_input", "reason": f"Invalid time_type: '{time_type}'. Must be 'PTO', 'WFH', or 'Sick'."}
    if not entries or not isinstance(entries, list):
        return {"status": "failure_invalid_input", "reason": "Invalid format for entries (must be a list)."}

    valid_entries = []
    for entry in entries:
        if not isinstance(entry, dict) or 'date' not in entry:
            return {"status": "failure_invalid_input", "reason": "Each entry must be an object with at least a 'date' key."}
        try:
            date_obj = datetime.strptime(entry['date'], '%Y-%m-%d').date()
            hours = entry.get('hours', 8) # Default to 8 hours if not specified
            if not isinstance(hours, int) or hours <= 0:
                 # WFH hours should also be positive now
                 # if time_type != "WFH" or hours < 0: # REMOVE THIS CONDITION
                 return {"status": "failure_invalid_input", "reason": f"Invalid hours '{hours}' for date {entry['date']}. Must be a positive number."}

            # Special case: WFH hours must always be 0 in TargetProcess Time entries # REMOVE THIS COMMENT AND LOGIC
            # original_hours_input = hours # Keep track for the result message
            # if time_type == "WFH":
            #     if hours != 0:
            #         print(f"INFO: Overriding hours to 0 for WFH log request on {entry['date']} (original input: {hours}).")
            #     hours = 0 # Enforce 0 hours for WFH time entries # REMOVE THIS LINE

            valid_entries.append({
                "date_obj": date_obj,
                "date_str": entry['date'],
                "hours": hours, # Use the validated hours directly
                "hours_input": hours # Store original for reporting (now same as hours)
            })
        except (ValueError, TypeError):
            return {"status": "failure_invalid_input", "reason": f"Invalid date format: '{entry.get('date', '')}'. Expected YYYY-MM-DD."}

    if not valid_entries:
         return {"status": "failure_invalid_input", "reason": "No valid entries provided for logging."}

    # --- Get TargetProcess ID ---
    targetprocess_id = get_targetprocess_id_by_slack_id(slack_id)
    if not targetprocess_id:
        # ... (standard user not linked error handling) ...
        logging.warning(f"Could not find TargetProcess ID for Slack user {slack_id}.")
        return {
            "status": "failure_user_not_linked",
            "reason": "Could not find a linked TargetProcess account for your Slack ID.",
            "error_details": f"No targetprocess_id found for slack_id {slack_id} in local DB."
        }
    print(f"DEBUG: Found TargetProcess ID {targetprocess_id} for Slack ID {slack_id}")

    # --- Get Story ID ---
    story_nicname_map = {"PTO": "PTO_STORY", "WFH": "WFH_STORY", "Sick": "SICK_STORY"}
    story_nicname = story_nicname_map.get(time_type)
    special_entities = potenza_api.get_special_entities_cache()
    story_id = None
    for entity in special_entities:
        if entity.get('entity_nicname') == story_nicname:
            story_id = entity.get('entity_id')
            break
    if not story_id:
        # ... (standard story ID not found error handling) ...
        logging.error(f"Could not find entity_id for {story_nicname} in special_entities cache.")
        return {
            "status": "failure_tool_error",
            "reason": f"Configuration error: Could not find the {time_type} story ID.",
            "error_details": f"{story_nicname} entity_id missing from special_entities cache."
        }
    print(f"DEBUG: Found {time_type} Story ID: {story_id}")

    # --- Get API Key ---
    tp_api_key = os.getenv("TP_API_KEY")
    if not tp_api_key:
        # ... (standard API key missing error handling) ...
        logging.error("TP_API_KEY environment variable not set.")
        return {
            "status": "failure_tool_error",
            "reason": "Configuration error: TargetProcess API key is missing.",
            "error_details": "TP_API_KEY environment variable not set."
        }

    # --- Log Each Entry ---
    results = []
    logged_count = 0
    skipped_weekend_count = 0
    failed_count = 0
    log_api_url = f"https://laneterralever.tpondemand.com/api/v1/Times?access_token={tp_api_key}&format=json"
    headers = {'Content-Type': 'application/json'}

    for entry_data in valid_entries:
        date_obj = entry_data["date_obj"]
        date_str = entry_data["date_str"]
        hours_to_log = entry_data["hours"] # Use the correct hours
        hours_input = entry_data["hours_input"] # For reporting

        item_result = {"date": date_str, "hours_input": hours_input}

        # Skip weekends
        if date_obj.weekday() >= 5: # 5 = Saturday, 6 = Sunday
            print(f"INFO: Skipping weekend date {date_str} for {time_type} logging.")
            item_result["status"] = "skipped_weekend"
            results.append(item_result)
            skipped_weekend_count += 1
            continue

        # Construct payload
        time_payload = {
            "Description": f"{time_type} logged via Slack Bot",
            "Spent": float(hours_to_log), # Use the correct hours
            "Remain": 0, # Typically 0 for logged time
            "Date": date_str, # YYYY-MM-DD format
            "Assignable": {"Id": story_id},
            "User": {"Id": targetprocess_id}
        }

        try:
            print(f"DEBUG: Logging {time_type} for {date_str}, {hours_to_log} hours. Payload: {json.dumps(time_payload)}")
            response = requests.post(log_api_url, headers=headers, json=time_payload, timeout=15)
            response.raise_for_status() # Check for HTTP errors

            if response.status_code == 201 or response.status_code == 200: # 201 Created or 200 OK
                print(f"DEBUG: Successfully logged {time_type} for {date_str}")
                item_result["status"] = "logged"
                item_result["api_response"] = response.json() # Store response if needed
                results.append(item_result)
                logged_count += 1
            else:
                # Should be caught by raise_for_status, but handle defensively
                print(f"WARNING: Log request for {date_str} returned status {response.status_code}, expected 200/201.")
                item_result["status"] = "failed"
                item_result["reason"] = f"API returned unexpected status: {response.status_code}"
                item_result["error_details"] = response.text[:200] # Limit error detail length
                results.append(item_result)
                failed_count += 1

        except requests.exceptions.HTTPError as http_err:
            logging.error(f"HTTP error logging {time_type} for {date_str}: {http_err}. Response: {response.text}")
            item_result["status"] = "failed"
            item_result["reason"] = f"API Error: {http_err}"
            item_result["error_details"] = response.text[:500] # Limit error detail length
            results.append(item_result)
            failed_count += 1
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Network error logging {time_type} for {date_str}: {req_err}")
            item_result["status"] = "failed"
            item_result["reason"] = f"Network Error: {req_err}"
            results.append(item_result)
            failed_count += 1
        except Exception as e:
            logging.exception(f"Unexpected error logging {time_type} for {date_str}")
            item_result["status"] = "failed"
            item_result["reason"] = f"Unexpected Error: {e}"
            results.append(item_result)
            failed_count += 1

    # --- Format Final Response ---
    overall_status = "success"
    message = f"{time_type} logging complete. Logged: {logged_count}, Skipped (weekend): {skipped_weekend_count}, Failed: {failed_count}."

    if failed_count > 0 and logged_count == 0 and skipped_weekend_count == 0:
        overall_status = "failure_tool_error" # All attempts failed
        message = f"Failed to log any {time_type} entries."
    elif failed_count > 0 or skipped_weekend_count > 0:
        overall_status = "partial_success" # Some succeeded, some failed/skipped
        message = f"{time_type} logging partially successful. Logged: {logged_count}, Skipped (weekend): {skipped_weekend_count}, Failed: {failed_count}."

    print(f"DEBUG: Final log_time_entry status: {overall_status}. Message: {message}")
    return {
        "status": overall_status,
        "message": message,
        "data": {"results": results}
    }

# --- Consolidated Deletion Function ---
def delete_time_entry(slack_id: str, time_type: str, dates_to_delete: list):
    """
    Finds and deletes existing time entries (PTO, WFH, Sick) for specific dates
    for the requesting user.

    Args:
        slack_id: The Slack ID of the user requesting the deletion.
        time_type: The type of time entry ("PTO", "WFH", "Sick").
        dates_to_delete: A list of date strings (YYYY-MM-DD) for which to find and delete entries.

    Returns:
        A dictionary containing the overall status and results for each deletion attempt.
    """
    print(f"Executing delete_time_entry for Slack ID: {slack_id}, Type: {time_type}, Dates: {dates_to_delete}")

    # --- Input Validation ---
    if not slack_id or not isinstance(slack_id, str):
        return {"status": "failure_invalid_input", "reason": "Invalid Slack ID provided internally."}
    if time_type not in ["PTO", "WFH", "Sick"]:
        return {"status": "failure_invalid_input", "reason": f"Invalid time_type: '{time_type}'. Must be 'PTO', 'WFH', or 'Sick'."}
    if not dates_to_delete or not isinstance(dates_to_delete, list):
        return {"status": "failure_invalid_input", "reason": "Invalid format for dates_to_delete (must be a list)."}

    valid_dates_to_delete = []
    for date_str in dates_to_delete:
        try:
            valid_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            valid_dates_to_delete.append(valid_date)
        except (ValueError, TypeError):
            return {"status": "failure_invalid_input", "reason": f"Invalid date format in list: '{date_str}'. Expected YYYY-MM-DD."}

    if not valid_dates_to_delete:
         return {"status": "failure_invalid_input", "reason": "No valid dates provided for deletion."}

    # --- Get TargetProcess ID ---
    targetprocess_id = get_targetprocess_id_by_slack_id(slack_id)
    if not targetprocess_id:
        # ... (standard user not linked error handling) ...
        logging.warning(f"Could not find TargetProcess ID for Slack user {slack_id}.")
        return {
            "status": "failure_user_not_linked",
            "reason": "Could not find a linked TargetProcess account for your Slack ID.",
            "error_details": f"No targetprocess_id found for slack_id {slack_id} in local DB."
        }
    print(f"DEBUG: Found TargetProcess ID {targetprocess_id} for Slack ID {slack_id}")

    # --- Get Story ID ---
    story_nicname_map = {"PTO": "PTO_STORY", "WFH": "WFH_STORY", "Sick": "SICK_STORY"}
    story_nicname = story_nicname_map.get(time_type)
    special_entities = potenza_api.get_special_entities_cache()
    story_id = None
    for entity in special_entities:
        if entity.get('entity_nicname') == story_nicname:
            story_id = entity.get('entity_id')
            break
    if not story_id:
        # ... (standard story ID not found error handling) ...
        logging.error(f"Could not find entity_id for {story_nicname} in special_entities cache.")
        return {
            "status": "failure_tool_error",
            "reason": f"Configuration error: Could not find the {time_type} story ID.",
            "error_details": f"{story_nicname} entity_id missing from special_entities cache."
        }
    print(f"DEBUG: Found {time_type} Story ID: {story_id}")

    # --- Get API Key ---
    tp_api_key = os.getenv("TP_API_KEY")
    if not tp_api_key:
        # ... (standard API key missing error handling) ...
        logging.error("TP_API_KEY environment variable not set.")
        return {
            "status": "failure_tool_error",
            "reason": "Configuration error: TargetProcess API key is missing.",
            "error_details": "TP_API_KEY environment variable not set."
        }

    # --- Find Matching Time IDs ---
    items_to_process = []
    not_found_dates = list(valid_dates_to_delete) # Copy list to track misses
    fetch_api_url = "https://laneterralever.tpondemand.com/svc/tp-apiv2-streaming-service/stream/userStories"
    fetch_params = {
        'select': f'{{times:times.select({{timeId:Id,spent,date,user.id,user.name}}).Where(id={targetprocess_id})}}',
        'where': f'(Id={story_id})',
        'access_token': tp_api_key
    }
    headers = {'Accept': 'application/json'}

    try:
        print(f"DEBUG: Fetching existing {time_type} times for user {targetprocess_id} on story {story_id}")
        response = requests.get(fetch_api_url, params=fetch_params, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()

        if data and 'items' in data and data['items'] and 'times' in data['items'][0]:
            for time_entry in data['items'][0]['times']:
                entry_date = parse_tp_date(time_entry.get('date'))
                time_id = time_entry.get('timeId')
                spent = time_entry.get('spent', 0) # Default spent to 0 if missing

                if entry_date and time_id and entry_date in valid_dates_to_delete:
                    items_to_process.append({
                        "date": entry_date.strftime('%Y-%m-%d'),
                        "timeId": time_id,
                        "hours": spent
                    })
                    if entry_date in not_found_dates:
                        not_found_dates.remove(entry_date) # Mark as found

    except requests.exceptions.RequestException as req_err:
        logging.error(f"Network error fetching {time_type} times: {req_err}")
        return {"status": "failure_tool_error", "reason": f"Network Error fetching existing {time_type} entries: {req_err}"}
    except Exception as e:
        logging.exception(f"Unexpected error fetching {time_type} times")
        return {"status": "failure_tool_error", "reason": f"An unexpected error occurred while finding entries: {e}"}

    # --- Handle Case Where No Matching Entries Found ---
    if not items_to_process:
        message = f"No existing {time_type} entries found to delete for the specified date(s): {', '.join(d.strftime('%Y-%m-%d') for d in valid_dates_to_delete)}."
        print(f"DEBUG: {message}")
        return {
            "status": "failure_not_found",
            "reason": message
        }

    # --- Attempt Deletion for Found Items ---
    deleted_items = []
    failed_items = []
    delete_api_base_url = "https://laneterralever.tpondemand.com/api/v1/times/"

    print(f"DEBUG: Attempting to delete {len(items_to_process)} {time_type} entries.")
    for item in items_to_process:
        time_id_to_delete = item['timeId']
        delete_url = f"{delete_api_base_url}{time_id_to_delete}"
        delete_params = {'access_token': tp_api_key}
        item_result = item.copy() # Copy item data for result tracking

        try:
            print(f"DEBUG: Deleting Time ID: {time_id_to_delete} ({time_type}) for date {item['date']}")
            delete_response = requests.delete(delete_url, params=delete_params, timeout=15)
            delete_response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)

            # Assuming 200 OK indicates success for DELETE
            if delete_response.status_code == 200:
                 print(f"DEBUG: Successfully deleted Time ID: {time_id_to_delete}")
                 item_result["status"] = "deleted"
                 deleted_items.append(item_result)
            else:
                 # Should be caught by raise_for_status, but handle defensively
                 print(f"WARNING: Delete request for Time ID {time_id_to_delete} returned status {delete_response.status_code}, expected 200.")
                 item_result["status"] = "failed"
                 item_result["reason"] = f"API returned unexpected status: {delete_response.status_code}"
                 item_result["error_details"] = delete_response.text[:200]
                 failed_items.append(item_result)

        except requests.exceptions.HTTPError as http_err:
            logging.error(f"HTTP error deleting Time ID {time_id_to_delete}: {http_err}. Response: {delete_response.text}")
            item_result["status"] = "failed"
            item_result["reason"] = f"API Error: {http_err}"
            item_result["error_details"] = delete_response.text[:500]
            failed_items.append(item_result)
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Network error deleting Time ID {time_id_to_delete}: {req_err}")
            item_result["status"] = "failed"
            item_result["reason"] = f"Network Error: {req_err}"
            failed_items.append(item_result)
        except Exception as e:
            logging.exception(f"Unexpected error deleting Time ID {time_id_to_delete}")
            item_result["status"] = "failed"
            item_result["reason"] = f"Unexpected Error: {e}"
            failed_items.append(item_result)

    # --- Format Final Response ---
    deleted_count = len(deleted_items)
    failed_count = len(failed_items)
    overall_status = "success"
    message = f"{time_type} deletion process complete. Deleted: {deleted_count}, Failed: {failed_count}."

    if failed_count > 0 and deleted_count == 0:
        overall_status = "failure_tool_error" # All attempts failed
        message = f"Failed to delete all {failed_count} identified {time_type} entries."
    elif failed_count > 0:
        overall_status = "partial_success" # Some succeeded, some failed
        message = f"{time_type} deletion partially successful. Deleted: {deleted_count}, Failed: {failed_count}."

    if not_found_dates:
         message += f" Could not find initial entries for: {', '.join(d.strftime('%Y-%m-%d') for d in not_found_dates)}."

    print(f"DEBUG: Final delete_time_entry status: {overall_status}. Message: {message}")
    return {
        "status": overall_status,
        "message": message,
        "data": {
            "deleted_items": deleted_items,
            "failed_items": failed_items
        }
    }

# --- Update Function (Handles Multiple Updates) ---
def update_time_entry(slack_id: str, time_type: str, updates: list):
    """
    Updates existing time entries (PTO, WFH, Sick) for the requesting user based on a list of update requests.
    Each update specifies an original date and the desired new date and/or hours.

    Args:
        slack_id: The Slack ID of the user requesting the update.
        time_type: The type of time entry ("PTO", "WFH", "Sick").
        updates: A list of dictionaries, each specifying an update.
                 Each dict must contain 'original_date' (YYYY-MM-DD) and at least one of
                 'new_date' (YYYY-MM-DD) or 'new_hours' (int).

    Returns:
        A dictionary containing the overall status and detailed results for each update attempt.
    """
    print(f"Executing update_time_entry for Slack ID: {slack_id}, Type: {time_type}, Updates: {updates}")

    # --- Input Validation ---
    if not slack_id or not isinstance(slack_id, str):
        return {"status": "failure_invalid_input", "reason": "Invalid Slack ID provided internally."}
    if time_type not in ["PTO", "WFH", "Sick"]:
        return {"status": "failure_invalid_input", "reason": f"Invalid time_type: '{time_type}'. Must be 'PTO', 'WFH', or 'Sick'."}
    if not updates or not isinstance(updates, list):
        return {"status": "failure_invalid_input", "reason": "Invalid format for updates (must be a list)."}

    valid_updates = []
    for update_request in updates:
        if not isinstance(update_request, dict) or 'original_date' not in update_request:
            return {"status": "failure_invalid_input", "reason": "Each update must be an object with at least an 'original_date' key."}

        original_date = update_request['original_date']
        new_date = update_request.get('new_date')
        new_hours = update_request.get('new_hours')

        if not new_date and new_hours is None:
            return {"status": "failure_invalid_input", "reason": f"Update for original date {original_date} failed: You must provide either 'new_date' or 'new_hours'."}

        try:
            original_dt = datetime.strptime(original_date, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return {"status": "failure_invalid_input", "reason": f"Invalid original_date format: '{original_date}'. Expected YYYY-MM-DD."}

        if new_date:
            try:
                datetime.strptime(new_date, '%Y-%m-%d') # Validate format
            except (ValueError, TypeError):
                return {"status": "failure_invalid_input", "reason": f"Invalid new_date format: '{new_date}' for original date {original_date}. Expected YYYY-MM-DD."}

        validated_new_hours = new_hours # Keep original input
        if new_hours is not None:
            if not isinstance(new_hours, int) or new_hours <= 0:
                # if time_type != "WFH" or new_hours < 0: # REMOVE THIS CONDITION
                return {"status": "failure_invalid_input", "reason": f"Invalid new_hours: '{new_hours}' for original date {original_date}. Must be a positive number."}

            # Special case: WFH hours must always be 0 # REMOVE THIS COMMENT AND LOGIC
            # if time_type == "WFH":
            #     if new_hours != 0:
            #         print(f"INFO: Overriding new_hours to 0 for WFH update request on {original_date} (original input: {new_hours}).")
            #     validated_new_hours = 0 # Enforce 0 hours for WFH time entries # REMOVE THIS LINE

        valid_updates.append({
            "original_date_obj": original_dt,
            "original_date_str": original_date,
            "new_date": new_date,
            "new_hours": validated_new_hours # Use the validated hours
        })

    if not valid_updates:
         return {"status": "failure_invalid_input", "reason": "No valid update requests provided."}

    # --- Get TargetProcess ID ---
    targetprocess_id = get_targetprocess_id_by_slack_id(slack_id)
    if not targetprocess_id:
        # ... (standard user not linked error handling) ...
        logging.warning(f"Could not find TargetProcess ID for Slack user {slack_id}.")
        return {
            "status": "failure_user_not_linked",
            "reason": "Could not find a linked TargetProcess account for your Slack ID.",
            "error_details": f"No targetprocess_id found for slack_id {slack_id} in local DB."
        }
    print(f"DEBUG: Found TargetProcess ID {targetprocess_id} for Slack ID {slack_id}")

    # --- Get Story ID ---
    story_nicname_map = {"PTO": "PTO_STORY", "WFH": "WFH_STORY", "Sick": "SICK_STORY"}
    story_nicname = story_nicname_map.get(time_type)
    special_entities = potenza_api.get_special_entities_cache()
    story_id = None
    for entity in special_entities:
        if entity.get('entity_nicname') == story_nicname:
            story_id = entity.get('entity_id')
            break
    if not story_id:
        # ... (standard story ID not found error handling) ...
        logging.error(f"Could not find entity_id for {story_nicname} in special_entities cache.")
        return {
            "status": "failure_tool_error",
            "reason": f"Configuration error: Could not find the {time_type} story ID.",
            "error_details": f"{story_nicname} entity_id missing from special_entities cache."
        }
    print(f"DEBUG: Found {time_type} Story ID: {story_id}")

    # --- Get API Key ---
    tp_api_key = os.getenv("TP_API_KEY")
    if not tp_api_key:
        # ... (standard API key missing error handling) ...
        logging.error("TP_API_KEY environment variable not set.")
        return {
            "status": "failure_tool_error",
            "reason": "Configuration error: TargetProcess API key is missing.",
            "error_details": "TP_API_KEY environment variable not set."
        }

    # --- Fetch Existing Entries for the User on this Story ---
    fetch_api_url = "https://laneterralever.tpondemand.com/svc/tp-apiv2-streaming-service/stream/userStories"
    fetch_params = {
        'select': f'{{times:times.select({{timeId:Id,spent,date,user.id,user.name}}).Where(id={targetprocess_id})}}',
        'where': f'(Id={story_id})',
        'access_token': tp_api_key
    }
    headers = {'Accept': 'application/json'}
    existing_times = {} # Dictionary to map date_obj -> time_entry

    try:
        print(f"DEBUG: Fetching existing {time_type} times for user {targetprocess_id} on story {story_id}")
        response = requests.get(fetch_api_url, params=fetch_params, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()

        if data and 'items' in data and data['items'] and 'times' in data['items'][0]:
            for time_entry_dict in data['items'][0]['times']:
                entry_date = parse_tp_date(time_entry_dict.get('date'))
                if entry_date:
                    if entry_date in existing_times:
                        logging.warning(f"Multiple {time_type} entries found for user {targetprocess_id} on {entry_date}. Using the first one found (TimeID: {existing_times[entry_date]['timeId']}).")
                    else:
                        existing_times[entry_date] = time_entry_dict
        print(f"DEBUG: Found {len(existing_times)} existing {time_type} entries for user {targetprocess_id}.")

    except requests.exceptions.RequestException as req_err:
        logging.error(f"Network error fetching {time_type} times: {req_err}")
        return {"status": "failure_tool_error", "reason": f"Network Error fetching existing {time_type} entries: {req_err}"}
    except Exception as e:
        logging.exception(f"Unexpected error fetching {time_type} times")
        return {"status": "failure_tool_error", "reason": f"An unexpected error occurred while finding existing entries: {e}"}

    # --- Process Each Update Request ---
    successful_updates = []
    failed_updates = []
    not_found_updates = []
    no_change_updates = []
    update_api_url = f"https://laneterralever.tpondemand.com/api/v1/times?access_token={tp_api_key}"
    update_headers = {'Content-Type': 'application/json'}

    for update_data in valid_updates:
        original_date_obj = update_data["original_date_obj"]
        original_date_str = update_data["original_date_str"]
        new_date = update_data["new_date"]
        new_hours = update_data["new_hours"]

        result_details = {"original_date": original_date_str}
        if new_date: result_details["requested_new_date"] = new_date
        if new_hours is not None: result_details["requested_new_hours"] = new_hours

        # Find the matching entry in the fetched list
        time_id_to_update = None
        original_tp_date_str = None
        original_spent = None
        found_entry = existing_times.get(original_date_obj)

        if found_entry:
            time_id_to_update = found_entry.get('timeId')
            original_spent = found_entry.get('spent')
            original_tp_date_str = found_entry.get('date')
            result_details["timeId"] = time_id_to_update
            print(f"DEBUG: Found matching entry to update for {original_date_str}. TimeID: {time_id_to_update}, Original Spent: {original_spent}")
        else:
            print(f"DEBUG: No matching {time_type} entry found for {original_date_str}")
            result_details["status"] = "not_found"
            result_details["reason"] = f"No {time_type} entry found for {original_date_str}"
            not_found_updates.append(result_details)
            continue

        # Construct Update Payload
        update_payload = {"Id": time_id_to_update}
        update_description_parts = []
        change_detected = False

        # Check if new_date is provided and different from original
        if new_date and new_date != original_date_obj.strftime('%Y-%m-%d'):
            update_payload["Date"] = new_date
            update_description_parts.append(f"date to {new_date}")
            change_detected = True

        # Check if new_hours is provided and different from original
        if new_hours is not None and float(new_hours) != original_spent:
            update_payload["Spent"] = float(new_hours)
            update_description_parts.append(f"hours to {new_hours}")
            change_detected = True

        if not change_detected:
            print(f"DEBUG: No changes detected for TimeID {time_id_to_update} on {original_date_str}. New values match existing.")
            result_details["status"] = "no_change_needed"
            result_details["reason"] = "The details provided already match the existing entry."
            no_change_updates.append(result_details)
            continue

        # Call Update API for this entry
        try:
            print(f"DEBUG: Updating Time ID: {time_id_to_update} ({original_date_str}) with payload: {json.dumps(update_payload)}")
            update_response = requests.post(update_api_url, headers=update_headers, json=update_payload, timeout=15)
            update_response.raise_for_status() # Check for HTTP errors

            if update_response.status_code == 200:
                print(f"DEBUG: Successfully updated Time ID: {time_id_to_update}")
                result_details["status"] = "updated"
                result_details["updated_fields"] = update_payload
                result_details["change_description"] = ", ".join(update_description_parts)
                successful_updates.append(result_details)
            else:
                # Should be caught by raise_for_status, but handle defensively
                logging.warning(f"Update request for Time ID {time_id_to_update} returned status {update_response.status_code}, expected 200.")
                result_details["status"] = "failed"
                result_details["reason"] = f"API returned unexpected status: {update_response.status_code}"
                result_details["error_details"] = update_response.text[:500]
                failed_updates.append(result_details)

        except requests.exceptions.HTTPError as http_err:
            logging.error(f"HTTP error updating Time ID {time_id_to_update}: {http_err}. Response: {update_response.text}")
            result_details["status"] = "failed"
            result_details["reason"] = f"API Error: {http_err}"
            result_details["error_details"] = update_response.text[:500]
            failed_updates.append(result_details)
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Network error updating Time ID {time_id_to_update}: {req_err}")
            result_details["status"] = "failed"
            result_details["reason"] = f"Network Error: {req_err}"
            failed_updates.append(result_details)
        except Exception as e:
            logging.exception(f"Unexpected error updating Time ID {time_id_to_update}")
            result_details["status"] = "failed"
            result_details["reason"] = f"Unexpected Error: {e}"
            failed_updates.append(result_details)

    # --- Format Final Response ---
    success_count = len(successful_updates)
    fail_count = len(failed_updates)
    not_found_count = len(not_found_updates)
    no_change_count = len(no_change_updates)
    total_requested = len(valid_updates)

    overall_status = "success"
    message_parts = []
    if success_count > 0: message_parts.append(f"Updated: {success_count}")
    if fail_count > 0: message_parts.append(f"Failed: {fail_count}")
    if not_found_count > 0: message_parts.append(f"Not Found: {not_found_count}")
    if no_change_count > 0: message_parts.append(f"No Change Needed: {no_change_count}")

    message = f"{time_type} update process complete. " + ", ".join(message_parts) + "."

    if fail_count > 0 and success_count == 0 and no_change_count == 0:
        overall_status = "failure_tool_error" # All attempts failed (excluding not found)
        message = f"Failed to update all {fail_count} requested {time_type} entries."
        if not_found_count > 0: message += f" Additionally, {not_found_count} entries were not found."
    elif fail_count > 0 or not_found_count > 0:
        overall_status = "partial_success" # Some succeeded/no_change, but also failures/not_found
    elif success_count == 0 and no_change_count > 0 and fail_count == 0 and not_found_count == 0:
         overall_status = "success" # Only no_change items, technically success
         message = f"No updates were needed for the {no_change_count} requested {time_type} entries as they already matched."
    elif success_count == 0 and no_change_count == 0 and fail_count == 0 and not_found_count > 0:
        overall_status = "failure_not_found" # Only not_found items
        message = f"Could not find any existing {time_type} entries for the {not_found_count} specified date(s)."


    print(f"DEBUG: Final update_time_entry status: {overall_status}. Message: {message}")
    return {
        "status": overall_status,
        "message": message,
        "data": {
            "successful_updates": successful_updates,
            "failed_updates": failed_updates,
            "not_found_updates": not_found_updates,
            "no_change_updates": no_change_updates
        }
    }

# --- Helper to Fetch Existing Times (Used by Delete/Update) ---
# NOTE: This helper is currently ONLY used by delete_time_entry.
# update_time_entry fetches directly. Consider refactoring update_time_entry
# to use this helper for consistency if desired, but the fix above works without it.
def fetch_existing_times(targetprocess_id: int, story_id: int, time_type: str, tp_api_key: str) -> Optional[list]:
    """
    Fetches existing time entries for a specific user on a specific story.

    Returns:
        A list of time entry dictionaries, or None if an error occurs.
        Each dictionary contains 'timeId', 'spent', 'date' (original TP string), 'parsed_date'.
    """
    # ... (API key check) ...

    fetch_api_url = "https://laneterralever.tpondemand.com/svc/tp-apiv2-streaming-service/stream/userStories"
    fetch_params = {
         # REVERTED Human Edit: Filter times by user.id, not time entry id
        'select': f'{{times:times.select({{timeId:Id,spent,date,user.id,user.name}}).Where(id={targetprocess_id})}}',
        'where': f'(Id={story_id})',
        'access_token': tp_api_key
    }
    headers = {'Accept': 'application/json'}
    fetched_times = [] # Return a list

    try:
        print(f"DEBUG [fetch_existing_times]: Fetching {time_type} times for user {targetprocess_id} on story {story_id}")
        response = requests.get(fetch_api_url, params=fetch_params, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()

        if data and 'items' in data and data['items'] and 'times' in data['items'][0]:
            for time_entry in data['items'][0]['times']:
                parsed_dt = parse_tp_date(time_entry.get('date'))
                if parsed_dt:
                    time_entry['parsed_date'] = parsed_dt # Add parsed date to the dict
                    fetched_times.append(time_entry)
                else:
                    logging.warning(f"Could not parse date for time entry: {time_entry}")
        print(f"DEBUG [fetch_existing_times]: Found {len(fetched_times)} entries.")
        return fetched_times # Return the list

    except requests.exceptions.RequestException as req_err:
        logging.error(f"Network error in fetch_existing_times: {req_err}")
        return None # Indicate error by returning None
    except Exception as e:
        logging.exception(f"Unexpected error in fetch_existing_times")
        return None # Indicate error by returning None


