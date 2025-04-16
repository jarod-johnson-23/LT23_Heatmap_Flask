import os
import json
import traceback
from datetime import datetime # <-- Import datetime
from app.slackbot.database import get_targetprocess_id_by_slack_id # Import the new helper
from app.slackbot.potenza import potenza_api # Import the Potenza API instance

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


