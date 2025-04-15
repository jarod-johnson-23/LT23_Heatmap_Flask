import os
import requests
import json
import time
from datetime import datetime, timedelta

class PotenzaAPI:
    """
    A class to handle interactions with the Potenza API, including authentication
    and executing SQL queries.
    """
    
    def __init__(self):
        self.base_url = "https://potenza.laneterralever.com"
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
            auth_url = f"{self.base_url}/authenticate"
            payload = {
                "email": self.email,
                "pass": self.password
            }
            
            response = self.session.post(auth_url, data=payload)
            
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
            dict: JSON response from the API, or error information
        """
        if not self.ensure_authenticated():
            return {"error": "Authentication failed"}
        
        try:
            # This is a placeholder - replace with actual API endpoint and parameters
            # when you have the details of how to execute SQL queries
            query_url = f"{self.base_url}/api/query"  # Adjust this endpoint as needed
            
            payload = {
                "query": sql_query
            }
            
            response = self.session.post(query_url, json=payload)
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"Query execution failed: {response.status_code} - {response.text}")
                return {
                    "error": f"Query execution failed with status code {response.status_code}",
                    "details": response.text
                }
                
        except Exception as e:
            print(f"Error executing query: {str(e)}")
            return {"error": str(e)}

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