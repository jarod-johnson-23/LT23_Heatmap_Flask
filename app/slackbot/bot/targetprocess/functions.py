import traceback
from datetime import datetime, date # Added date
from app.slackbot.potenza import potenza_api # Import the Potenza API instance
import re # Import regex for basic validation
import requests # Added requests
import os # Added os
import logging # Added logging
import json # Added json
import sqlite3 # Added sqlite3

# --- Database Helper ---
# Assume your DB is in the project root or adjust path as needed
DATABASE_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'slack_bot.db') # Adjust path if needed

def get_targetprocess_id_from_slack_id(slack_id: str) -> int | None:
    """Looks up the targetprocess_id for a given slack_id in the local DB."""
    targetprocess_id = None
    conn = None # Initialize conn to None
    try:
        if not os.path.exists(DATABASE_PATH):
             logging.error(f"Database file not found at: {DATABASE_PATH}")
             return None
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT targetprocess_id FROM authenticated_users WHERE slack_id = ?", (slack_id,))
        result = cursor.fetchone()
        if result and result[0]:
            targetprocess_id = int(result[0])
            logging.info(f"Found targetprocess_id {targetprocess_id} for slack_id {slack_id}")
        else:
            logging.warning(f"No targetprocess_id found for slack_id {slack_id} in authenticated_users table.")
    except sqlite3.Error as e:
        logging.error(f"Database error while fetching targetprocess_id for slack_id {slack_id}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error fetching targetprocess_id for slack_id {slack_id}: {e}")
    finally:
        if conn:
            conn.close()
    return targetprocess_id

# --- TargetProcess Functions ---

def get_current_cycles():
    """
    Retrieves information about the current cycle(s) based on the 'is_now' flag.

    Returns:
        A dictionary containing the status and a list of current cycles
        (each with name, start date, end date, and end serial day), or an error message.
    """
    print("Executing get_current_cycles")

    # 1. Define the SQL query (selecting specific columns)
    sql_query = """
        SELECT name, cycle_start, cycle_end, cycle_end_serial_day
        FROM cycles
        WHERE is_now = 1
        ORDER BY cycle_start DESC; -- Optional: order if multiple cycles might be 'now'
    """

    # 2. Execute the query using Potenza API
    try:
        print(f"DEBUG: Executing Potenza SQL for current cycles:\n{sql_query}")
        result = potenza_api.execute_sql(sql_query)
        print(f"DEBUG: Potenza Result: {result}")

        # 3. Process the result
        if isinstance(result, list):
            current_cycles = []
            for row in result:
                # Extract relevant data
                cycle_info = {
                    "name": row.get('name'),
                    "start_date": row.get('cycle_start'),
                    "end_date": row.get('cycle_end'),
                    "end_serial_day": row.get('cycle_end_serial_day')
                }
                # Basic validation
                if all(cycle_info.values()): # Check if all expected keys have non-None values
                     current_cycles.append(cycle_info)
                else:
                     print(f"WARNING: Skipping cycle row with missing data: {row}")

            message = f"Found {len(current_cycles)} current cycle(s)."
            if not current_cycles:
                 message = "No current cycles found marked with 'is_now = 1'."

            print(f"DEBUG: {message}")

            # 4. Format success response
            return {
                "status": "success",
                "message": message,
                "data": {
                    "current_cycles": current_cycles # Return the list of cycle details
                }
            }
        else:
            # Handle unexpected result format from Potenza
            print(f"ERROR: Unexpected result format from Potenza API for current cycles: {result}")
            return {
                "status": "failure_tool_error",
                "reason": "Received an unexpected response format while fetching current cycle data.",
                "error_details": f"Unexpected Potenza result type: {type(result)}"
            }

    except Exception as e:
        print(f"ERROR: Failed to execute Potenza SQL or process result for current cycles: {e}")
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for current cycles.",
            "error_details": str(e)
        }

def get_cycle_by_date(date_iso: str):
    """
    Retrieves cycle information active on a specific date.
    Assumes the input date is already validated and formatted as YYYY-MM-DD.

    Args:
        date_iso: The date to check, formatted as YYYY-MM-DD.

    Returns:
        A dictionary containing the status and a list of cycles active on that date,
        or an error message.
    """
    print(f"Executing get_cycle_by_date for ISO date: '{date_iso}'")

    # 1. Basic validation of the input format (already done by LLM, but good practice)
    if not date_iso or not isinstance(date_iso, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_iso):
        print(f"ERROR: Invalid date format received by function: '{date_iso}'. Expected YYYY-MM-DD.")
        # This indicates an error in the LLM's processing or the tool definition
        return {
            "status": "failure_tool_error", # Or potentially failure_invalid_input, but implies LLM error
            "reason": f"Internal error: Received improperly formatted date '{date_iso}'. Expected YYYY-MM-DD.",
            "error_details": "Date format validation failed within the function."
        }
    # Optional: Further validation like checking if it's a real date
    try:
        datetime.strptime(date_iso, '%Y-%m-%d')
    except ValueError:
        print(f"ERROR: Invalid date value received by function: '{date_iso}'.")
        return {
            "status": "failure_tool_error",
            "reason": f"Internal error: Received invalid date value '{date_iso}'.",
            "error_details": "Date value validation failed within the function."
        }


    # 2. Define the SQL query using the validated date
    sql_query = f"""
        SELECT name, cycle_start, cycle_end, cycle_end_serial_day
        FROM cycles
        WHERE cycle_start <= '{date_iso}'
          AND cycle_end >= '{date_iso}'
        ORDER BY cycle_start DESC;
    """

    # 3. Execute the query using Potenza API
    try:
        print(f"DEBUG: Executing Potenza SQL for cycles on date '{date_iso}':\n{sql_query}")
        result = potenza_api.execute_sql(sql_query)
        print(f"DEBUG: Potenza Result: {result}")

        # 4. Process the result
        if isinstance(result, list):
            cycles_found = []
            for row in result:
                cycle_info = {
                    "name": row.get('name'),
                    "start_date": row.get('cycle_start'),
                    "end_date": row.get('cycle_end'),
                    "end_serial_day": row.get('cycle_end_serial_day')
                }
                if all(cycle_info.values()):
                     cycles_found.append(cycle_info)
                else:
                     print(f"WARNING: Skipping cycle row with missing data for date {date_iso}: {row}")

            message = f"Found {len(cycles_found)} cycle(s) active on {date_iso}."
            if not cycles_found:
                 message = f"No active cycles found covering the date {date_iso}."

            print(f"DEBUG: {message}")

            # 5. Format success response
            return {
                "status": "success",
                "message": message,
                "data": {
                    "query_date": date_iso,
                    "cycles_on_date": cycles_found
                }
            }
        else:
            # Handle unexpected result format
            print(f"ERROR: Unexpected result format from Potenza API for cycles on date '{date_iso}': {result}")
            return {
                "status": "failure_tool_error",
                "reason": f"Received an unexpected response format while fetching cycle data for {date_iso}.",
                "error_details": f"Unexpected Potenza result type: {type(result)}"
            }

    except Exception as e:
        print(f"ERROR: Failed to execute Potenza SQL or process result for cycles on date '{date_iso}': {e}")
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": f"An error occurred while communicating with the data source for cycles on {date_iso}.",
            "error_details": str(e)
        }

def get_cycle_details_by_name(name: str):
    """
    Retrieves details for a specific cycle based on its name (number).

    Args:
        name: The name/number of the cycle to look up (e.g., "1269", "2261").

    Returns:
        A dictionary containing the status and details of the found cycle
        (name, start date, end date, start serial day), or an error message.
    """
    print(f"Executing get_cycle_details_by_name for cycle name: '{name}'")

    # 1. Validate input
    if not name or not isinstance(name, str) or not name.strip().isdigit():
        print(f"ERROR: Invalid cycle name provided: '{name}'. Expected a numeric string.")
        return {
            "status": "failure_invalid_input",
            "reason": f"Invalid cycle name provided: '{name}'. Please provide the cycle number (e.g., '1269').",
            "missing_parameters": ["name"] # Or indicate invalid parameter value
        }

    cycle_name = name.strip()

    # 2. Define the SQL query
    #    Ensure the name is treated correctly in the WHERE clause (might be text or number in DB)
    #    Using parameterization is safer if Potenza API supports it, otherwise ensure quoting if text.
    #    Assuming 'name' is numeric based on examples. If it's text, use quotes: WHERE name='{cycle_name}'
    sql_query = f"""
        SELECT name, cycle_start, cycle_end, cycle_start_serial_day
        FROM cycles
        WHERE name = {cycle_name}
    """

    # 3. Execute the query using Potenza API
    try:
        print(f"DEBUG: Executing Potenza SQL for cycle details for name '{cycle_name}':\n{sql_query}")
        result = potenza_api.execute_sql(sql_query)
        print(f"DEBUG: Potenza Result: {result}")

        # 4. Process the result
        if isinstance(result, list) and len(result) > 0:
            # Expecting only one cycle for a specific name
            if len(result) > 1:
                 print(f"WARNING: Found multiple cycles with the name '{cycle_name}'. Returning the first one.")

            cycle_data = result[0]
            cycle_details = {
                "name": cycle_data.get('name'),
                "start_date": cycle_data.get('cycle_start'),
                "end_date": cycle_data.get('cycle_end'),
                "start_serial_day": cycle_data.get('cycle_start_serial_day') # Days until start
            }

            # Basic validation of returned data
            if not all(str(v) for k, v in cycle_details.items() if k != 'start_serial_day' or v is not None): # Allow start_serial_day to be 0 or None maybe? Check DB constraints. Assuming it should exist.
                 print(f"WARNING: Cycle data retrieved for '{cycle_name}' has missing fields: {cycle_details}")
                 # Decide how to handle - return error or partial data? Let's return error for now.
                 return {
                    "status": "failure_tool_error",
                    "reason": f"Retrieved incomplete data for cycle '{cycle_name}'.",
                    "error_details": "Missing expected fields in database response."
                 }

            message = f"Successfully retrieved details for cycle {cycle_name}."
            print(f"DEBUG: {message}")

            # 5. Format success response
            return {
                "status": "success",
                "message": message,
                "data": {
                    "cycle_details": cycle_details
                }
            }
        elif isinstance(result, list) and len(result) == 0:
            # No cycle found with that name
            message = f"No cycle found with the name '{cycle_name}'."
            print(f"DEBUG: {message}")
            return {
                "status": "failure_no_data_found",
                "reason": message
            }
        else:
            # Handle unexpected result format
            print(f"ERROR: Unexpected result format from Potenza API for cycle details '{cycle_name}': {result}")
            return {
                "status": "failure_tool_error",
                "reason": f"Received an unexpected response format while fetching details for cycle {cycle_name}.",
                "error_details": f"Unexpected Potenza result type: {type(result)}"
            }

    except Exception as e:
        print(f"ERROR: Failed to execute Potenza SQL or process result for cycle details '{cycle_name}': {e}")
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": f"An error occurred while communicating with the data source for cycle {cycle_name}.",
            "error_details": str(e)
        }

def get_latest_cycle_completion():
    """
    Retrieves the completion statistics (dollars and percentage) for the most
    recently finished cycle (based on cycle_sequence_num = -1 and end date within last 7 days).

    Returns:
        A dictionary containing the status and completion details (cycle name,
        completed dollars, completed percentage), or an error message.
    """
    print("Executing get_latest_cycle_completion")

    # 1. Define the SQL query
    sql_query = """
        SELECT cycle,
               sum( case when is_final = 1 or status = 'Finished' then earned_value_dollars else 0 END)
                 as completed_dollars,
               ROUND(100*sum( case when is_final = 1 or status = 'Finished' then earned_value_dollars else 0 END) /
                             (
                              select sum(earned_value_dollars)
                              from stories_in_cycle_baseline_accreted
                              where cycle in (
                                              select max(name)
                                              from cycles
                                              where cycle_sequence_num = -1 and
                                                    cycle_end >= (select day from calendar_lookup where serial_day = -7)
                                             )
                             ),1)
                  as completed_pct

        FROM stories
        WHERE cycle_sequence_num = -1
        AND cycle in (
                        select max(name)
                        from cycles
                        where cycle_sequence_num = -1 and
                              cycle_end >= (select day from calendar_lookup where serial_day = -7)
                     )
        GROUP BY cycle
    """

    # 2. Execute the query using Potenza API
    try:
        print(f"DEBUG: Executing Potenza SQL for latest cycle completion:\n{sql_query}")
        result = potenza_api.execute_sql(sql_query)
        print(f"DEBUG: Potenza Result: {result}")

        # 3. Process the result
        if isinstance(result, list) and len(result) > 0:
            # Expecting only one row for the most recent cycle matching criteria
            if len(result) > 1:
                 print(f"WARNING: Found multiple rows for latest cycle completion query. Using the first one. Result: {result}")

            completion_data = result[0]
            completion_details = {
                "cycle_name": completion_data.get('cycle'),
                "completed_dollars": completion_data.get('completed_dollars'),
                "completed_percentage": completion_data.get('completed_pct')
            }

            # Basic validation of returned data
            if not all(v is not None for k, v in completion_details.items()):
                 print(f"WARNING: Latest cycle completion data retrieved has missing fields: {completion_details}")
                 return {
                    "status": "failure_tool_error",
                    "reason": "Retrieved incomplete completion data for the latest cycle.",
                    "error_details": "Missing expected fields in database response."
                 }

            message = f"Successfully retrieved completion stats for the latest finished cycle ({completion_details['cycle_name']})."
            print(f"DEBUG: {message}")

            # 4. Format success response
            return {
                "status": "success",
                "message": message,
                "data": {
                    "latest_cycle_completion": completion_details
                }
            }
        elif isinstance(result, list) and len(result) == 0:
            # No cycle found matching the criteria (e.g., no cycle ended recently)
            message = "No recently completed cycle found (cycle_sequence_num = -1 and ended within last 7 days)."
            print(f"DEBUG: {message}")
            return {
                "status": "failure_no_data_found",
                "reason": message
            }
        else:
            # Handle unexpected result format
            print(f"ERROR: Unexpected result format from Potenza API for latest cycle completion: {result}")
            return {
                "status": "failure_tool_error",
                "reason": "Received an unexpected response format while fetching latest cycle completion.",
                "error_details": f"Unexpected Potenza result type: {type(result)}"
            }

    except ZeroDivisionError:
        # Specific handling if the denominator (total baseline dollars) is zero
        print(f"ERROR: Division by zero encountered in latest cycle completion query. Likely zero baseline dollars for the cycle.")
        # Attempt to find the cycle name if possible from a separate query or assume it's missing
        # For now, return a specific error
        return {
            "status": "failure_tool_error", # Or maybe failure_no_data_found with specific reason
            "reason": "Could not calculate completion percentage for the latest cycle, possibly due to zero baseline value.",
            "error_details": "Division by zero in SQL calculation."
        }
    except Exception as e:
        print(f"ERROR: Failed to execute Potenza SQL or process result for latest cycle completion: {e}")
        traceback.print_exc()
        return {
            "status": "failure_tool_error",
            "reason": "An error occurred while communicating with the data source for latest cycle completion.",
            "error_details": str(e)
        }

def log_pto(slack_id: str, pto_entries: list):
    """
    Logs Paid Time Off (PTO) entries in TargetProcess for the user associated
    with the given Slack ID.

    Args:
        slack_id: The Slack ID of the user logging PTO.
        pto_entries: A list of dictionaries, where each dictionary represents a PTO day
                     and must contain 'date' (YYYY-MM-DD string) and optionally
                     'hours' (integer, defaults to 8).

    Returns:
        A dictionary containing the overall status and results for each entry.
    """
    print(f"Executing log_pto for Slack ID: {slack_id} with {len(pto_entries)} entries.")

    # --- Input Validation ---
    if not slack_id or not isinstance(slack_id, str):
         return {"status": "failure_invalid_input", "reason": "Invalid Slack ID provided internally."} # Should not happen if framework passes it
    if not pto_entries or not isinstance(pto_entries, list):
        return {"status": "failure_invalid_input", "reason": "Invalid format for PTO entries (must be a list)."}

    # --- Get TargetProcess ID from Slack ID ---
    targetprocess_id = get_targetprocess_id_from_slack_id(slack_id)
    if not targetprocess_id:
        logging.warning(f"Could not find TargetProcess ID for Slack user {slack_id}.")
        return {
            "status": "failure_tool_error", # Or a more specific status like 'failure_user_not_linked'
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
    # Construct base URL without token
    tp_api_url_base = "https://laneterralever.tpondemand.com/api/v1/times"
    # Define headers *without* Authorization
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    success_count = 0
    skipped_count = 0
    failed_count = 0

    for entry in pto_entries:
        entry_date_str = entry.get('date')
        entry_hours = entry.get('hours', 8) # Default to 8 hours
        entry_result = {"date": entry_date_str, "hours": entry_hours, "status": "pending"}

        # Validate date format and value
        try:
            pto_date = datetime.strptime(entry_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Invalid date format: '{entry_date_str}'. Expected YYYY-MM-DD."
            failed_count += 1
            results.append(entry_result)
            continue

        # Validate hours
        if not isinstance(entry_hours, int) or entry_hours <= 0:
             entry_result["status"] = "failed"
             entry_result["reason"] = f"Invalid hours value: '{entry_hours}'. Expected a positive integer."
             failed_count += 1
             results.append(entry_result)
             continue

        # Check if weekend (Monday is 0, Sunday is 6)
        if pto_date.weekday() >= 5: # Saturday or Sunday
            entry_result["status"] = "skipped_weekend"
            entry_result["reason"] = "Date falls on a weekend."
            skipped_count += 1
            results.append(entry_result)
            continue

        # Prepare API Payload
        payload = {
            "Spent": entry_hours,
            "Remain": 0,
            "Description": "PTO [[logged via slack bot]]",
            "Date": entry_date_str, # Use the validated string
            "User": {
                "Id": targetprocess_id
            },
            "Assignable": {
                "Id": pto_story_id
            }
        }

        # Make API Call - Pass token in URL parameters
        try:
            print(f"DEBUG: Logging PTO - Date: {entry_date_str}, Hours: {entry_hours}, User: {targetprocess_id}")
            # Add access_token as a query parameter
            response = requests.post(
                tp_api_url_base, # Use the base URL
                params={'access_token': tp_api_key}, # <-- Pass token here
                headers=headers, # Headers without Authorization
                json=payload,
                timeout=20
            )
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

            # Check response content if needed, assume 2xx means success for now
            entry_result["status"] = "logged"
            entry_result["api_response"] = response.json() # Store response if needed
            success_count += 1
            print(f"DEBUG: Successfully logged PTO for {entry_date_str}")

        except requests.exceptions.HTTPError as http_err:
            entry_result["status"] = "failed"
            error_content = "No details available"
            try:
                error_content = response.json() # Try to get JSON error details
            except json.JSONDecodeError:
                error_content = response.text # Otherwise get raw text
            entry_result["reason"] = f"API Error: {http_err}"
            entry_result["api_response"] = error_content
            failed_count += 1
            logging.error(f"Failed to log PTO for {entry_date_str}. Status: {response.status_code}, Response: {error_content}")

        except requests.exceptions.RequestException as req_err:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Network Error: {req_err}"
            failed_count += 1
            logging.error(f"Network error logging PTO for {entry_date_str}: {req_err}")

        except Exception as e:
            entry_result["status"] = "failed"
            entry_result["reason"] = f"Unexpected Error: {e}"
            failed_count += 1
            logging.exception(f"Unexpected error logging PTO for {entry_date_str}")

        results.append(entry_result)

    # --- Determine Overall Status and Message ---
    overall_status = "success"
    if failed_count > 0 and success_count == 0 and skipped_count == 0:
        overall_status = "failure_tool_error" # All failed
    elif failed_count > 0:
        overall_status = "partial_success" # Some failed

    message = f"PTO logging complete. Logged: {success_count}, Skipped (weekend): {skipped_count}, Failed: {failed_count}."
    if overall_status == "failure_tool_error":
         message = f"PTO logging failed for all {failed_count} entries."
    elif overall_status == "partial_success":
         message = f"PTO logging partially successful. Logged: {success_count}, Skipped (weekend): {skipped_count}, Failed: {failed_count}."


    print(f"DEBUG: {message}")
    return {
        "status": overall_status,
        "message": message,
        "data": {
            "results": results
        }
    }

# --- Add other TargetProcess functions below ---
# e.g., get_story_details, get_project_info, etc.
