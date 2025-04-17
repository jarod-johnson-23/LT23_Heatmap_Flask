import os
import json
import traceback
from datetime import datetime, date
from app.slackbot.database import get_targetprocess_id_by_slack_id # Import the new helper
from app.slackbot.potenza import potenza_api # Import the Potenza API instance
import re
import requests
import logging
import sqlite3
from typing import Union

# Configure logging if not already done in this module scope
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Database Helper ---
# Assume your DB is in the project root or adjust path as needed
DATABASE_PATH = os.path.join(os.path.dirname(__file__), '..', 'data/conversations.db') # Adjust path if needed

# --- PTO/WFH Functions ---
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
        return {
            "status": "failure_user_not_found_local",
            "reason": "Could not find your TargetProcess ID link in my records. Have you authenticated successfully?",
            "slack_id": slack_id
        }

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
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source.",
            "error_details": str(e)
        }

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
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for today's PTO list.",
            "error_details": str(e)
        }

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
                 message = f"No upcoming PTO entries found matching the name '{search_name}'."

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
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for upcoming PTO.",
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
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for today's WFH list.",
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
                 message = f"No upcoming WFH entries found matching the name '{search_name}'."

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
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for upcoming WFH.",
            "error_details": str(e)
        }

def log_pto(slack_id: str, pto_entries: list):
    """
    Logs Paid Time Off (PTO) entries in TargetProcess for the user associated
    with the given Slack ID. Ensures dates are always in the future.

    Args:
        slack_id: The Slack ID of the user logging PTO.
        pto_entries: A list of dictionaries, where each dictionary represents a PTO day
                     and must contain 'date' (YYYY-MM-DD string) and optionally
                     'hours' (integer, defaults to 8). The year in the date string
                     will be adjusted to the nearest future occurrence.

    Returns:
        A dictionary containing the overall status and results for each entry.
    """
    print(f"Executing log_pto for Slack ID: {slack_id} with {len(pto_entries)} entries.")

    # --- Input Validation ---
    if not slack_id or not isinstance(slack_id, str):
         return {"status": "failure_invalid_input", "reason": "Invalid Slack ID provided internally."}
    if not pto_entries or not isinstance(pto_entries, list):
        return {"status": "failure_invalid_input", "reason": "Invalid format for PTO entries (must be a list)."}

    # --- Get TargetProcess ID from Slack ID ---
    targetprocess_id = get_targetprocess_id_by_slack_id(slack_id)
    if not targetprocess_id:
        logging.warning(f"Could not find TargetProcess ID for Slack user {slack_id}.")
        return {
            "status": "failure_tool_error", # Or failure_user_not_linked
            "reason": "Could not find a linked TargetProcess account for your Slack ID. Please ensure you have authenticated.",
            "error_details": f"No targetprocess_id found for slack_id {slack_id} in local DB."
        }
    print(f"DEBUG: Found TargetProcess ID {targetprocess_id} for Slack ID {slack_id}")

    # --- Get PTO Story ID from Cache ---
    special_entities = potenza_api.get_special_entities_cache()
    pto_story_id = None
    for entity in special_entities:
        if entity.get('entity_nicname') == 'PTO_STORY':
            pto_story_id = entity.get('entity_id')
            break

    if not pto_story_id:
        logging.error("Could not find entity_id for PTO_STORY in special_entities cache.")
        return {
            "status": "failure_tool_error",
            "reason": "Configuration error: Could not find the PTO story ID.",
            "error_details": "PTO_STORY entity_id missing from special_entities cache."
        }
    print(f"DEBUG: Found PTO Story ID: {pto_story_id}")

    # --- Get TargetProcess API Key ---
    tp_api_key = os.getenv("TP_API_KEY")
    if not tp_api_key:
        logging.error("TP_API_KEY environment variable not set.")
        return {
            "status": "failure_tool_error",
            "reason": "Configuration error: TargetProcess API key is missing.",
            "error_details": "TP_API_KEY environment variable not set."
        }

    # --- Process Each Entry ---
    results = []
    tp_api_url_base = "https://laneterralever.tpondemand.com/api/v1/times"
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    success_count = 0
    skipped_count = 0
    failed_count = 0
    today = date.today() # Get today's date once

    for entry in pto_entries:
        original_date_str = entry.get('date') # Date string from LLM (might have wrong year)
        entry_hours = entry.get('hours', 8) # Default to 8 hours
        # Initialize result with the original date for reference if needed
        entry_result = {"original_date_input": original_date_str, "hours": entry_hours, "status": "pending"}

        # --- Date Validation and Year Correction ---
        try:
            # Parse the date provided by the LLM
            llm_date = datetime.strptime(original_date_str, '%Y-%m-%d').date()

            # Determine the correct year
            target_year = today.year
            # If the month/day has already passed this year, use next year
            if (llm_date.month, llm_date.day) < (today.month, today.day):
                target_year += 1

            # Construct the corrected date
            corrected_date = date(target_year, llm_date.month, llm_date.day)
            corrected_date_str = corrected_date.strftime('%Y-%m-%d')

            # Ensure the corrected date is not in the past
            if corrected_date < today:
                 entry_result["status"] = "failed"
                 entry_result["reason"] = f"Cannot log PTO for a past date ({corrected_date_str})."
                 entry_result["corrected_date"] = corrected_date_str # Add corrected date for clarity
                 failed_count += 1
                 results.append(entry_result)
                 print(f"DEBUG: Skipping past date {corrected_date_str} (original: {original_date_str})")
                 continue

            # Update entry_result with the corrected date
            entry_result["date"] = corrected_date_str
            print(f"DEBUG: Corrected date from '{original_date_str}' to '{corrected_date_str}'")

        except (ValueError, TypeError) as e:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Invalid date format received: '{original_date_str}'. Expected YYYY-MM-DD. Error: {e}"
            failed_count += 1
            results.append(entry_result)
            continue
        # --- End Date Validation ---

        # Validate hours
        if not isinstance(entry_hours, int) or entry_hours <= 0:
             entry_result["status"] = "failed"
             entry_result["reason"] = f"Invalid hours value: '{entry_hours}'. Expected a positive integer."
             failed_count += 1
             results.append(entry_result)
             continue

        # Check if weekend using the *corrected* date
        if corrected_date.weekday() >= 5: # Saturday or Sunday
            entry_result["status"] = "skipped_weekend"
            entry_result["reason"] = "Date falls on a weekend."
            skipped_count += 1
            results.append(entry_result)
            continue

        # Prepare API Payload using the *corrected* date string
        payload = {
            "Spent": entry_hours,
            "Remain": 0,
            "Description": "PTO [[logged via slack bot]]",
            "Date": corrected_date_str, # Use corrected date
            "User": { "Id": targetprocess_id },
            "Assignable": { "Id": pto_story_id }
        }

        # Make API Call - Pass token in URL parameters
        try:
            print(f"DEBUG: Logging PTO - Date: {corrected_date_str}, Hours: {entry_hours}, User: {targetprocess_id}")
            response = requests.post(
                tp_api_url_base,
                params={'access_token': tp_api_key},
                headers=headers,
                json=payload,
                timeout=20
            )
            response.raise_for_status()

            entry_result["status"] = "logged"
            entry_result["api_response"] = response.json()
            success_count += 1
            print(f"DEBUG: Successfully logged PTO for {corrected_date_str}")

        except requests.exceptions.HTTPError as http_err:
            entry_result["status"] = "failed"
            error_content = "No details available"
            try: error_content = response.json()
            except json.JSONDecodeError: error_content = response.text
            entry_result["reason"] = f"API Error: {http_err}"
            entry_result["api_response"] = error_content
            failed_count += 1
            logging.error(f"Failed to log PTO for {corrected_date_str}. Status: {response.status_code}, Response: {error_content}")
        except requests.exceptions.RequestException as req_err:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Network Error: {req_err}"
            failed_count += 1
            logging.error(f"Network error logging PTO for {corrected_date_str}: {req_err}")
        except Exception as e:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Unexpected Error: {e}"
            failed_count += 1
            logging.exception(f"Unexpected error logging PTO for {corrected_date_str}")

        results.append(entry_result)

    # --- Determine Overall Status and Message ---
    overall_status = "success"
    if failed_count > 0 and success_count == 0 and skipped_count == 0:
        overall_status = "failure_tool_error"
    elif failed_count > 0:
        overall_status = "partial_success"

    message = f"PTO logging complete. Logged: {success_count}, Skipped (weekend): {skipped_count}, Failed: {failed_count}."
    if overall_status == "failure_tool_error":
         message = f"PTO logging failed for all {failed_count} entries."
    elif overall_status == "partial_success":
         message = f"PTO logging partially successful. Logged: {success_count}, Skipped (weekend): {skipped_count}, Failed: {failed_count}."

    print(f"DEBUG: {message}")
    return {
        "status": overall_status,
        "message": message,
        "data": { "results": results }
    }

def log_wfh(slack_id: str, wfh_entries: list):
    """
    Logs Work From Home (WFH) entries in TargetProcess for the user associated
    with the given Slack ID. Ensures dates are always in the future.

    Args:
        slack_id: The Slack ID of the user logging WFH.
        wfh_entries: A list of dictionaries, where each dictionary represents a WFH day
                     and must contain 'date' (YYYY-MM-DD string). The year in the date string
                     will be adjusted to the nearest future occurrence.

    Returns:
        A dictionary containing the overall status and results for each entry.
    """
    print(f"Executing log_wfh for Slack ID: {slack_id} with {len(wfh_entries)} entries.")

    # --- Input Validation ---
    if not slack_id or not isinstance(slack_id, str):
         return {"status": "failure_invalid_input", "reason": "Invalid Slack ID provided internally."}
    if not wfh_entries or not isinstance(wfh_entries, list):
        return {"status": "failure_invalid_input", "reason": "Invalid format for WFH entries (must be a list)."}

    # --- Get TargetProcess ID from Slack ID ---
    targetprocess_id = get_targetprocess_id_by_slack_id(slack_id)
    if not targetprocess_id:
        logging.warning(f"Could not find TargetProcess ID for Slack user {slack_id}.")
        return {
            "status": "failure_tool_error", # Or failure_user_not_linked
            "reason": "Could not find a linked TargetProcess account for your Slack ID. Please ensure you have authenticated.",
            "error_details": f"No targetprocess_id found for slack_id {slack_id} in local DB."
        }
    print(f"DEBUG: Found TargetProcess ID {targetprocess_id} for Slack ID {slack_id}")

    # --- Get WFH Story ID from Cache ---
    special_entities = potenza_api.get_special_entities_cache()
    wfh_story_id = None
    for entity in special_entities:
        if entity.get('entity_nicname') == 'WORK_FROM_HOME_STORY':
            wfh_story_id = entity.get('entity_id')
            break

    if not wfh_story_id:
        logging.error("Could not find entity_id for WORK_FROM_HOME_STORY in special_entities cache.")
        return {
            "status": "failure_tool_error",
            "reason": "Configuration error: Could not find the WFH story ID.",
            "error_details": "WORK_FROM_HOME_STORY entity_id missing from special_entities cache."
        }
    print(f"DEBUG: Found WFH Story ID: {wfh_story_id}")

    # --- Get TargetProcess API Key ---
    tp_api_key = os.getenv("TP_API_KEY")
    if not tp_api_key:
        logging.error("TP_API_KEY environment variable not set.")
        return {
            "status": "failure_tool_error",
            "reason": "Configuration error: TargetProcess API key is missing.",
            "error_details": "TP_API_KEY environment variable not set."
        }

    # --- Process Each Entry ---
    results = []
    tp_api_url_base = "https://laneterralever.tpondemand.com/api/v1/times"
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    success_count = 0
    skipped_count = 0
    failed_count = 0
    today = date.today() # Get today's date once

    for entry in wfh_entries:
        original_date_str = entry.get('date')
        entry_hours = entry.get('hours', 8) # Default to 8 hours, even if ignored later
        # Initialize result
        entry_result = {
            "original_date_input": original_date_str,
            "hours_input": entry_hours, # Store provided hours
            "status": "pending"
        }

        # --- Date Validation and Year Correction ---
        try:
            llm_date = datetime.strptime(original_date_str, '%Y-%m-%d').date()
            target_year = today.year
            if (llm_date.month, llm_date.day) < (today.month, today.day):
                target_year += 1
            corrected_date = date(target_year, llm_date.month, llm_date.day)
            corrected_date_str = corrected_date.strftime('%Y-%m-%d')
            if corrected_date < today:
                 entry_result["status"] = "failed"
                 entry_result["reason"] = f"Cannot log WFH for a past date ({corrected_date_str})."
                 entry_result["corrected_date"] = corrected_date_str
                 failed_count += 1
                 results.append(entry_result)
                 print(f"DEBUG: Skipping past date {corrected_date_str} for WFH (original: {original_date_str})")
                 continue
            entry_result["date"] = corrected_date_str # Store corrected date
            print(f"DEBUG: Corrected WFH date from '{original_date_str}' to '{corrected_date_str}'")

        except (ValueError, TypeError) as e:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Invalid date format received for WFH: '{original_date_str}'. Expected YYYY-MM-DD. Error: {e}"
            failed_count += 1
            results.append(entry_result)
            continue
        # --- End Date Validation ---

        # Validate hours (even though we might ignore the value for WFH API call)
        if not isinstance(entry_hours, int) or entry_hours <= 0:
             entry_result["status"] = "failed"
             entry_result["reason"] = f"Invalid hours value received: '{entry_hours}'. Expected a positive integer."
             failed_count += 1
             results.append(entry_result)
             continue

        # Check if weekend using the *corrected* date
        if corrected_date.weekday() >= 5: # Saturday or Sunday
            entry_result["status"] = "skipped_weekend"
            entry_result["reason"] = "Date falls on a weekend."
            skipped_count += 1
            results.append(entry_result)
            continue

        # Prepare API Payload using the *corrected* date string
        payload = {
            "Spent": 0, # Always 0 for WFH time entries
            "Remain": 0,
            "Description": f"WFH ({entry_hours}h requested) [[logged via slack bot]]", # Optionally include requested hours in description
            "Date": corrected_date_str,
            "User": { "Id": targetprocess_id },
            "Assignable": { "Id": wfh_story_id }
        }

        # Make API Call - Pass token in URL parameters
        try:
            # Debug message doesn't need hours as Spent is 0
            print(f"DEBUG: Logging WFH - Date: {corrected_date_str}, User: {targetprocess_id}")
            response = requests.post(
                tp_api_url_base,
                params={'access_token': tp_api_key},
                headers=headers,
                json=payload,
                timeout=20
            )
            response.raise_for_status()

            entry_result["status"] = "logged"
            entry_result["api_response"] = response.json()
            success_count += 1
            print(f"DEBUG: Successfully logged WFH for {corrected_date_str}")

        except requests.exceptions.HTTPError as http_err:
            entry_result["status"] = "failed"
            error_content = "No details available"
            try: error_content = response.json()
            except json.JSONDecodeError: error_content = response.text
            entry_result["reason"] = f"API Error: {http_err}"
            entry_result["api_response"] = error_content
            failed_count += 1
            logging.error(f"Failed to log WFH for {corrected_date_str}. Status: {response.status_code}, Response: {error_content}")
        except requests.exceptions.RequestException as req_err:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Network Error: {req_err}"
            failed_count += 1
            logging.error(f"Network error logging WFH for {corrected_date_str}: {req_err}")
        except Exception as e:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Unexpected Error: {e}"
            failed_count += 1
            logging.exception(f"Unexpected error logging WFH for {corrected_date_str}")

        results.append(entry_result)

    # --- Determine Overall Status and Message ---
    overall_status = "success"
    if failed_count > 0 and success_count == 0 and skipped_count == 0:
        overall_status = "failure_tool_error"
    elif failed_count > 0:
        overall_status = "partial_success"

    message = f"WFH logging complete. Logged: {success_count}, Skipped (weekend): {skipped_count}, Failed: {failed_count}."
    if overall_status == "failure_tool_error":
         message = f"WFH logging failed for all {failed_count} entries."
    elif overall_status == "partial_success":
         message = f"WFH logging partially successful. Logged: {success_count}, Skipped (weekend): {skipped_count}, Failed: {failed_count}."

    print(f"DEBUG: {message}")
    return {
        "status": overall_status,
        "message": message,
        "data": { "results": results }
    }

def log_sick(slack_id: str, sick_entries: list):
    """
    Logs Sick Time entries in TargetProcess for the user associated
    with the given Slack ID. Ensures dates are always in the future.

    Args:
        slack_id: The Slack ID of the user logging sick time.
        sick_entries: A list of dictionaries, where each dictionary represents a sick day
                      and must contain 'date' (YYYY-MM-DD string) and optionally
                      'hours' (integer, defaults to 8). The year in the date string
                      will be adjusted to the nearest future occurrence.

    Returns:
        A dictionary containing the overall status and results for each entry.
    """
    print(f"Executing log_sick for Slack ID: {slack_id} with {len(sick_entries)} entries.")

    # --- Input Validation ---
    if not slack_id or not isinstance(slack_id, str):
         return {"status": "failure_invalid_input", "reason": "Invalid Slack ID provided internally."}
    if not sick_entries or not isinstance(sick_entries, list):
        return {"status": "failure_invalid_input", "reason": "Invalid format for Sick Time entries (must be a list)."}

    # --- Get TargetProcess ID from Slack ID ---
    targetprocess_id = get_targetprocess_id_by_slack_id(slack_id)
    if not targetprocess_id:
        logging.warning(f"Could not find TargetProcess ID for Slack user {slack_id}.")
        return {
            "status": "failure_tool_error", # Or failure_user_not_linked
            "reason": "Could not find a linked TargetProcess account for your Slack ID. Please ensure you have authenticated.",
            "error_details": f"No targetprocess_id found for slack_id {slack_id} in local DB."
        }
    print(f"DEBUG: Found TargetProcess ID {targetprocess_id} for Slack ID {slack_id}")

    # --- Get Sick Story ID from Cache ---
    special_entities = potenza_api.get_special_entities_cache()
    sick_story_id = None
    for entity in special_entities:
        if entity.get('entity_nicname') == 'SICK_STORY':
            sick_story_id = entity.get('entity_id')
            break

    if not sick_story_id:
        logging.error("Could not find entity_id for SICK_STORY in special_entities cache.")
        return {
            "status": "failure_tool_error",
            "reason": "Configuration error: Could not find the Sick Time story ID.",
            "error_details": "SICK_STORY entity_id missing from special_entities cache."
        }
    print(f"DEBUG: Found Sick Story ID: {sick_story_id}")

    # --- Get TargetProcess API Key ---
    tp_api_key = os.getenv("TP_API_KEY")
    if not tp_api_key:
        logging.error("TP_API_KEY environment variable not set.")
        return {
            "status": "failure_tool_error",
            "reason": "Configuration error: TargetProcess API key is missing.",
            "error_details": "TP_API_KEY environment variable not set."
        }

    # --- Process Each Entry ---
    results = []
    tp_api_url_base = "https://laneterralever.tpondemand.com/api/v1/times"
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    success_count = 0
    skipped_count = 0
    failed_count = 0
    today = date.today() # Get today's date once

    for entry in sick_entries:
        original_date_str = entry.get('date')
        entry_hours = entry.get('hours', 8) # Default to 8 hours
        # Initialize result
        entry_result = {
            "original_date_input": original_date_str,
            "hours_input": entry_hours,
            "status": "pending"
        }

        # --- Date Validation and Year Correction ---
        try:
            llm_date = datetime.strptime(original_date_str, '%Y-%m-%d').date()
            target_year = today.year
            if (llm_date.month, llm_date.day) < (today.month, today.day):
                target_year += 1
            corrected_date = date(target_year, llm_date.month, llm_date.day)
            corrected_date_str = corrected_date.strftime('%Y-%m-%d')
            if corrected_date < today:
                 entry_result["status"] = "failed"
                 entry_result["reason"] = f"Cannot log Sick Time for a past date ({corrected_date_str})."
                 entry_result["corrected_date"] = corrected_date_str
                 failed_count += 1
                 results.append(entry_result)
                 print(f"DEBUG: Skipping past date {corrected_date_str} for Sick Time (original: {original_date_str})")
                 continue
            entry_result["date"] = corrected_date_str
            print(f"DEBUG: Corrected Sick Time date from '{original_date_str}' to '{corrected_date_str}'")

        except (ValueError, TypeError) as e:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Invalid date format received for Sick Time: '{original_date_str}'. Expected YYYY-MM-DD. Error: {e}"
            failed_count += 1
            results.append(entry_result)
            continue
        # --- End Date Validation ---

        # Validate hours
        if not isinstance(entry_hours, int) or entry_hours <= 0:
             entry_result["status"] = "failed"
             entry_result["reason"] = f"Invalid hours value: '{entry_hours}'. Expected a positive integer."
             failed_count += 1
             results.append(entry_result)
             continue

        # Check if weekend using the *corrected* date
        if corrected_date.weekday() >= 5: # Saturday or Sunday
            entry_result["status"] = "skipped_weekend"
            entry_result["reason"] = "Date falls on a weekend."
            skipped_count += 1
            results.append(entry_result)
            continue

        # Prepare API Payload using the *corrected* date string
        payload = {
            "Spent": entry_hours, # Use provided hours for Sick Time
            "Remain": 0,
            "Description": "Sick Time [[logged via slack bot]]",
            "Date": corrected_date_str,
            "User": { "Id": targetprocess_id },
            "Assignable": { "Id": sick_story_id }
        }

        # Make API Call - Pass token in URL parameters
        try:
            print(f"DEBUG: Logging Sick Time - Date: {corrected_date_str}, Hours: {entry_hours}, User: {targetprocess_id}")
            response = requests.post(
                tp_api_url_base,
                params={'access_token': tp_api_key},
                headers=headers,
                json=payload,
                timeout=20
            )
            response.raise_for_status()

            entry_result["status"] = "logged"
            entry_result["api_response"] = response.json()
            success_count += 1
            print(f"DEBUG: Successfully logged Sick Time for {corrected_date_str}")

        except requests.exceptions.HTTPError as http_err:
            entry_result["status"] = "failed"
            error_content = "No details available"
            try: error_content = response.json()
            except json.JSONDecodeError: error_content = response.text
            entry_result["reason"] = f"API Error: {http_err}"
            entry_result["api_response"] = error_content
            failed_count += 1
            logging.error(f"Failed to log Sick Time for {corrected_date_str}. Status: {response.status_code}, Response: {error_content}")
        except requests.exceptions.RequestException as req_err:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Network Error: {req_err}"
            failed_count += 1
            logging.error(f"Network error logging Sick Time for {corrected_date_str}: {req_err}")
        except Exception as e:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Unexpected Error: {e}"
            failed_count += 1
            logging.exception(f"Unexpected error logging Sick Time for {corrected_date_str}")

        results.append(entry_result)

    # --- Determine Overall Status and Message ---
    overall_status = "success"
    if failed_count > 0 and success_count == 0 and skipped_count == 0:
        overall_status = "failure_tool_error"
    elif failed_count > 0:
        overall_status = "partial_success"

    message = f"Sick Time logging complete. Logged: {success_count}, Skipped (weekend): {skipped_count}, Failed: {failed_count}."
    if overall_status == "failure_tool_error":
         message = f"Sick Time logging failed for all {failed_count} entries."
    elif overall_status == "partial_success":
         message = f"Sick Time logging partially successful. Logged: {success_count}, Skipped (weekend): {skipped_count}, Failed: {failed_count}."

    print(f"DEBUG: {message}")
    return {
        "status": overall_status,
        "message": message,
        "data": { "results": results }
    }


