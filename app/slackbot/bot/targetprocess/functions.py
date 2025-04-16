import traceback
from datetime import datetime # <-- Import datetime for date parsing
from app.slackbot.potenza import potenza_api # Import the Potenza API instance
import re # Import regex for basic validation

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

# --- Add other TargetProcess functions below ---
# e.g., get_story_details, get_project_info, etc.
