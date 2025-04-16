import os
import requests
import json
import time
import re
from datetime import datetime, timedelta

class PotenzaAPI:
    """
    A class to handle interactions with the Potenza API, including authentication
    and executing SQL queries.
    """
    
    def __init__(self):
        self.base_url = "https://potenza.laneterralever.com"
        self.auth_url = os.getenv("POTENZA_AUTH_URL", f"{self.base_url}/authenticate")
        self.query_url = os.getenv("POTENZA_QUERY_URL", f"{self.base_url}/process-op")
        self.session = requests.Session()
        self.auth_token = None
        self.token_expiry = None
        self.email = os.getenv("POTENZA_EMAIL")
        self.password = os.getenv("POTENZA_PASSWORD")
        
        if not self.email or not self.password:
            print("WARNING: Potenza API credentials not found in environment variables")
    
    def _is_token_valid(self):
        """Check if the current auth token is still valid."""
        if not self.auth_token or not self.token_expiry:
            return False
        
        # Add a 5-minute buffer to ensure we refresh before expiration
        buffer_time = timedelta(minutes=5)
        return datetime.now() < (self.token_expiry - buffer_time)
    
    def authenticate(self):
        """Authenticate with the Potenza API and get a session token."""
        try:
            payload = {
                "email": self.email,
                "pass": self.password
            }
            
            response = self.session.post(self.auth_url, data=payload)
            
            if response.status_code == 200:
                # Check for cookies that might contain session info
                cookies = self.session.cookies
                
                # Set token expiry (assuming a 24-hour token life by default)
                # This should be adjusted based on actual token expiration policy
                self.token_expiry = datetime.now() + timedelta(hours=24)
                self.auth_token = True  # Using session cookies, so just mark as authenticated
                
                print("Successfully authenticated with Potenza API")
                return True
            else:
                print(f"Authentication failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"Error during authentication: {str(e)}")
            return False
    
    def ensure_authenticated(self):
        """Ensure we have a valid authentication token before making API calls."""
        if not self._is_token_valid():
            return self.authenticate()
        return True
    
    def execute_sql(self, sql_query):
        """
        Execute a SQL query against the Potenza database and return the results as JSON.
        
        Args:
            sql_query (str): The SQL query to execute
            
        Returns:
            dict: JSON response with query results or error information
        """
        if not self.ensure_authenticated():
            return {"error": "Authentication failed"}
        
        try:
            # Prepare the form data as specified
            payload = {
                "Paste your select query here.": sql_query,
                "Temporary access query key": "",  # Leave empty as specified
                "title": "Ops Support Tools",
                "op_name": "Research tool -> JSON"
            }
            
            # Make the API request
            response = self.session.post(self.query_url, data=payload)
            
            if response.status_code == 200:
                # Parse the unique response format
                return self._parse_query_response(response.text)
            else:
                print(f"Query execution failed: {response.status_code} - {response.text}")
                return {
                    "error": f"Query execution failed with status code {response.status_code}",
                    "details": response.text
                }
                
        except Exception as e:
            print(f"Error executing query: {str(e)}")
            return {"error": str(e)}
    
    def _parse_query_response(self, response_text):
        """
        Parse the unique response format from the Potenza API.
        
        The response is in the format:
        {"file":false,"error":false,"text":"<pre>\n{JSON DATA}\n</pre>"}
        
        We need to extract the JSON data between <pre> and </pre> tags.
        """
        try:
            # First, parse the outer JSON
            response_json = json.loads(response_text)
            
            # Check for errors in the response
            if response_json.get("error"):
                return {"error": "API reported an error", "details": response_json}
            
            # Extract the text content
            text_content = response_json.get("text", "")
            
            # Use regex to extract content between <pre> and </pre> tags
            match = re.search(r'<pre>\s*(.+?)\s*</pre>', text_content, re.DOTALL)
            if not match:
                return {"error": "Could not extract data from response", "raw_response": text_content}
            
            # Get the JSON data
            json_data = match.group(1)
            
            try:
                # Try to parse as JSON directly
                parsed_data = json.loads(json_data)
                
                # Check if the parsed data has a "rows" key with array of arrays format
                if "rows" in parsed_data and isinstance(parsed_data["rows"], list) and len(parsed_data["rows"]) > 0:
                    # Transform the rows array into our preferred format
                    columns = parsed_data["rows"][0]
                    data = []
                    
                    for i in range(1, len(parsed_data["rows"])):
                        row_dict = {}
                        for j in range(len(columns)):
                            if j < len(parsed_data["rows"][i]):  # Ensure we don't go out of bounds
                                row_dict[columns[j]] = parsed_data["rows"][i][j]
                            else:
                                row_dict[columns[j]] = None
                        data.append(row_dict)
                    
                    # Return only the data array and row count
                    return data
                
                return parsed_data
            except json.JSONDecodeError:
                # If it's not valid JSON, try to parse the custom format
                return self._parse_custom_format(json_data)
                
        except Exception as e:
            print(f"Error parsing query response: {str(e)}")
            return {"error": f"Failed to parse response: {str(e)}", "raw_response": response_text}
    
    def _parse_custom_format(self, data_str):
        """
        Parse the custom format where data is an array of arrays.
        First array contains column names, subsequent arrays contain values.
        
        Returns a list of dictionaries, each representing a row with column names as keys.
        """
        try:
            # Try to extract the rows array
            rows_match = re.search(r'"rows":\s*(\[.+?\])', data_str, re.DOTALL)
            if rows_match:
                rows_str = rows_match.group(1)
                # Replace single quotes with double quotes for JSON parsing
                rows_str = rows_str.replace("'", '"')
                rows = json.loads(rows_str)
            else:
                # If no rows array, try to parse the whole string as a JSON array
                rows = json.loads(data_str)
            
            # If we don't have at least one row (the header), return empty result
            if not rows or len(rows) < 1:
                return []
            
            # First row contains column names
            columns = rows[0]
            
            # Convert rows to dictionaries
            result = []
            for i in range(1, len(rows)):
                row_dict = {}
                for j in range(len(columns)):
                    if j < len(rows[i]):  # Ensure we don't go out of bounds
                        row_dict[columns[j]] = rows[i][j]
                    else:
                        row_dict[columns[j]] = None
                result.append(row_dict)
            
            # Return only the data array
            return result
            
        except Exception as e:
            print(f"Error parsing custom format: {str(e)}")
            return {"error": f"Failed to parse custom format: {str(e)}", "raw_data": data_str}

# Create a singleton instance for use throughout the application
potenza_api = PotenzaAPI()

def execute_sql_query(sql):
    """
    Public function to execute SQL queries against the Potenza database.
    This is the main entry point that should be used by other modules.
    
    Args:
        sql (str): The SQL query to execute
        
    Returns:
        dict: JSON response with query results or error information
    """
    return potenza_api.execute_sql(sql) 