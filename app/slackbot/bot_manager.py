import os
import json
from pathlib import Path
from datetime import datetime
from openai import OpenAI
import importlib  # <-- Add this import for dynamic loading
import traceback # <-- Add this for error logging
import inspect # <-- Add this import
from app.slackbot.database import log_tool_usage # <-- Import the logging function
import re

# Initialize OpenAI client
client = OpenAI(
    organization=os.getenv("openai_organization"),
    api_key=os.getenv("openai_api_key"),
)

# Define the base bot directory
BOT_DIR = Path(__file__).parent / "bot"

# --- Potenza API Singleton (Moved here for better access by functions) ---
# We need access to the Potenza API instance if sub-bots need it.
# It's better to manage shared resources like this centrally.
try:
    from .potenza import potenza_api
except ImportError:
    print("Warning: Potenza API module not found or failed to import.")
    potenza_api = None
# --- End Potenza API Singleton ---


class BotManager:
    """
    Manages the main bot and its sub-bots, handling delegation and responses.
    """
    
    def __init__(self):
        """Initialize the bot manager with the main bot and available sub-bots."""
        self.model = os.getenv("SLACKBOT_OPENAI_MODEL", "gpt-4.1")
        self.main_bot_instructions = self._load_instructions(BOT_DIR / "instructions.txt")
        self.main_bot_tools = self._load_tools(BOT_DIR / "tools.json")
        
        # Discover available sub-bots
        self.sub_bots = self._discover_sub_bots()
        
        # Initialize slack client to None - will be set by routes.py
        self.slack_client = None
        self.ephemeral_msg_timestamps = {}  # Store message timestamps by channel/user
    
    def _load_instructions(self, path):
        """Load bot instructions from a file."""
        print(f"DEBUG: Loading instructions for delegate botfrom {path}")
        try:
            with open(path, 'r') as f:
                instructions = f.read().strip()
                
                # Add current date information
                current_date = datetime.now().strftime("%Y-%m-%d")
                # Ensure date isn't added twice if already present
                if f"Today's date is" not in instructions:
                     instructions = f"{instructions}\n\nToday's date is {current_date}."
                
                return instructions
        except Exception as e:
            print(f"Error loading bot instructions from {path}: {e}")
            current_date = datetime.now().strftime("%Y-%m-%d")
            return f"You are a helpful assistant. Today's date is {current_date}."
    
    def _load_tools(self, path):
        """Load bot tools from a JSON file."""
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading bot tools from {path}: {e}")
            return []
    
    def _discover_sub_bots(self):
        """Discover available sub-bots in the bot directory."""
        sub_bots = {}
        
        try:
            # Look for subdirectories in the bot directory
            for item in BOT_DIR.iterdir():
                if item.is_dir():
                    bot_name = item.name
                    instructions_path = item / "instructions.txt"
                    tools_path = item / "tools.json"
                    functions_path = item / "functions.py" # Check for functions file
                    
                    # Only add if instructions, tools, and functions exist
                    if instructions_path.exists() and tools_path.exists() and functions_path.exists():
                        sub_bots[bot_name] = {
                            "name": bot_name,
                            "path": item, # Store the path to the bot's directory
                            "instructions_path": instructions_path,
                            "tools_path": tools_path,
                            "functions_path": functions_path
                        }
                    elif instructions_path.exists() and tools_path.exists():
                         print(f"Warning: Sub-bot '{bot_name}' found but missing functions.py. It won't be able to execute tools.")
                         # Optionally add it anyway if manager bot might use it without tools
                         # sub_bots[bot_name] = { ... }
        except Exception as e:
            print(f"Error discovering sub-bots: {e}")
        
        return sub_bots
    
    def process_message(self, user_message, user_email, slack_id, previous_response_id=None, channel_id=None):
        """
        Process a user message through the main bot and delegate to sub-bots if needed.
        
        Args:
            user_message (str): The message from the user
            user_email (str): The user's email address
            slack_id (str): The user's Slack ID
            previous_response_id (str, optional): The previous response ID for conversation continuity
            channel_id (str, optional): The Slack channel ID (required for ephemeral messages)
            
        Returns:
            dict: The response containing text and/or function calls
        """
        # Personalize instructions for the main bot
        current_datetime = datetime.now()
        current_date = current_datetime.strftime('%Y-%m-%d')
        day_of_week = current_datetime.strftime('%A')
        personalized_instructions = f"{self.main_bot_instructions}\n\nYou are chatting with {user_email}. Today is {day_of_week}, {current_date}."
        
        # Initial call to the main bot
        try:
            print(f"DEBUG: Calling Main Bot (Model: {self.model})")
            response = client.responses.create(
                model=self.model,
                instructions=personalized_instructions,
                previous_response_id=previous_response_id,
                input=[{"role": "user", "content": user_message}],
                tools=self.main_bot_tools
            )
            print(f"DEBUG: Main Bot Response ID: {response.id}")
            
            # Check if the manager bot wants to delegate to a sub-bot
            for output_item in response.output:
                if output_item.type == "text":
                    text_content = output_item.text.value
                    # Check for delegation pattern in the text output
                    if "Delegate to `" in text_content:
                        # Extract the delegate bot name and message
                        # This pattern is based on the examples in the instructions.txt
                        match = re.search(r"Delegate to `(\w+)` bot with text \"(.+)\"", text_content)
                        if match:
                            delegate_name = match.group(1)
                            delegate_message = match.group(2)
                            
                            # Send ephemeral message before delegation
                            if channel_id:
                                self._send_delegation_status(channel_id, slack_id, delegate_name, "start")
                            
                            print(f"DEBUG: Manager Bot delegating to '{delegate_name}' with message: '{delegate_message}'")
                            
                            # Process with the delegate bot
                            delegate_response = self._process_with_sub_bot(
                                delegate_name,
                                delegate_message,
                                user_email,
                                slack_id
                            )
                            
                            # Remove ephemeral message after delegation
                            if channel_id:
                                self._send_delegation_status(channel_id, slack_id, delegate_name, "end")
                            
                            # If delegate response is an error (dict with 'error' key)
                            if isinstance(delegate_response, dict) and "error" in delegate_response:
                                print(f"DEBUG: Delegate Bot '{delegate_name}' returned error: {delegate_response['error']}")
                                # Just pass the error back to the main bot to handle
                                final_response = client.responses.create(
                                    model=self.model,
                                    instructions=personalized_instructions,
                                    previous_response_id=response.id,
                                    input=[{
                                        "role": "user", 
                                        "content": f"The {delegate_name} delegate bot encountered an error: {delegate_response['error']}"
                                    }]
                                )
                                return final_response
                                
                            # Handle valid responses from sub-bot
                            # ... existing code for handling sub-bot responses ...
            
            # If no delegation, return the original response from the main bot
            print("DEBUG: No delegation requested by Main Bot.")
            return response
            
        except Exception as e:
            print(f"Error processing message with main bot: {e}")
            traceback.print_exc() # Print full traceback for debugging
            # Return an error structure consistent with OpenAI response object if possible
            # This part might need refinement based on how errors should be presented
            return {"error": str(e), "output": [{"content": [{"text": f"An internal error occurred: {e}"}]}]}
    
    def _execute_sub_bot_function(self, bot_name, function_name, function_args, user_email, slack_id):
        """Dynamically load and execute a function from a sub-bot's functions.py,
           passing user_email and slack_id only if the function expects them,
           and log the function call."""
        try:
            sub_bot_config = self.sub_bots.get(bot_name)
            if not sub_bot_config:
                return {"error": f"Sub-bot '{bot_name}' configuration not found."}

            functions_path = sub_bot_config["functions_path"]
            module_name = f"app.slackbot.bot.{bot_name}.functions" # Module path relative to project root

            print(f"DEBUG: Attempting to load module: {module_name}")
            bot_module = importlib.import_module(module_name)
            print(f"DEBUG: Module {module_name} loaded successfully.")

            if hasattr(bot_module, function_name):
                function_to_call = getattr(bot_module, function_name)
                print(f"DEBUG: Preparing to execute function '{function_name}' from {module_name}")

                # --- Inspect function signature and build final arguments ---
                sig = inspect.signature(function_to_call)
                func_params = sig.parameters
                final_args = function_args.copy()
                if 'user_email' in func_params:
                    final_args['user_email'] = user_email
                    # print(f"DEBUG: Adding 'user_email' to args for {function_name}") # Optional debug
                if 'slack_id' in func_params:
                    final_args['slack_id'] = slack_id
                    # print(f"DEBUG: Adding 'slack_id' to args for {function_name}") # Optional debug
                if 'potenza_api' in func_params:
                    if potenza_api:
                        final_args['potenza_api'] = potenza_api
                        # print(f"DEBUG: Adding 'potenza_api' to args for {function_name}") # Optional debug
                    else:
                        return {"error": f"Function '{function_name}' requires Potenza API, which is not configured."}

                # --- Execute the function with the constructed arguments ---
                print(f"DEBUG: Executing '{function_name}' with final args: {list(final_args.keys())}")
                result = function_to_call(**final_args)
                # --- End function execution ---

                # --- Log the tool usage after executing ---
                try:
                    log_tool_usage(function_name, user_email, slack_id)
                except Exception as log_e:
                    print(f"WARNING: Failed to log tool usage for {function_name} due to: {log_e}")
                # --- End logging ---

                print(f"DEBUG: Function '{function_name}' executed. Result: {result}")
                # Ensure result is JSON serializable
                try:
                    json.dumps(result)
                    return result
                except TypeError:
                     print(f"ERROR: Result of function '{function_name}' is not JSON serializable.")
                     return {"error": "Function result is not JSON serializable", "function": function_name}

            else:
                # ... (handle function not found) ...
                print(f"ERROR: Function '{function_name}' not found in {module_name}")
                return {"error": f"Function '{function_name}' not found in sub-bot '{bot_name}'."}

        except ImportError as e:
             print(f"ERROR: Could not import module {module_name}: {e}")
             traceback.print_exc()
             return {"error": f"Could not load functions for sub-bot '{bot_name}'. Import error."}
        except Exception as e:
            print(f"ERROR: Error executing function '{function_name}' for sub-bot '{bot_name}': {e}")
            traceback.print_exc()
            return {"error": f"Error executing function '{function_name}': {str(e)}"}
    
    def _process_with_sub_bot(self, bot_name, message, user_email, slack_id):
        """
        Process a message with a specific sub-bot, handling its internal function calls.
        Returns the *final* OpenAI response object after all processing.
        """
        try:
            sub_bot = self.sub_bots.get(bot_name)
            if not sub_bot:
                return {"error": f"Sub-bot '{bot_name}' not found"} # Return error dict

            instructions = self._load_instructions(sub_bot["instructions_path"])
            tools = self._load_tools(sub_bot["tools_path"])

            now = datetime.now()
            current_date = now.strftime('%Y-%m-%d')
            day_of_week = now.strftime('%A')
            personalized_instructions = f"{instructions}\n\nYou are processing a task for {user_email}. The current date is {current_date} ({day_of_week})."

            print(f"DEBUG: Calling Sub-Bot '{bot_name}' (Model: {self.model}) with message: '{message}' and slack_id: {slack_id}")
            # Initial call to the sub-bot
            response = client.responses.create(
                model=self.model,
                instructions=personalized_instructions,
                input=[{"role": "user", "content": message}],
                tools=tools
            )
            print(f"DEBUG: Sub-Bot '{bot_name}' First Response ID: {response.id}")
            # print(f"DEBUG: Sub-Bot '{bot_name}' First Output: {response.output}") # Verbose

            # Check for function calls from the sub-bot
            function_calls_to_process = []
            for output_item in response.output:
                print(f"DEBUG: Output item: {output_item}")
                if hasattr(output_item, 'type') and output_item.type == "function_call":
                    function_calls_to_process.append(output_item)

            # If the sub-bot made function calls, execute them
            if function_calls_to_process:
                print(f"DEBUG: Sub-Bot '{bot_name}' requested {len(function_calls_to_process)} function call(s).")
                function_results = []
                for func_call in function_calls_to_process:
                    function_name = func_call.name
                    try:
                         function_args = json.loads(func_call.arguments)
                    except json.JSONDecodeError:
                         print(f"ERROR: Invalid JSON arguments for function {function_name}: {func_call.arguments}")
                         function_result = {"error": "Invalid JSON arguments provided"}
                    else:
                         # Execute the function dynamically
                         function_result = self._execute_sub_bot_function(
                             bot_name,
                             function_name,
                             function_args,
                             user_email, # Pass user email context
                             slack_id # Pass slack id context
                         )

                    function_results.append({
                        "type": "function_call_output",
                        "call_id": func_call.call_id,
                        "output": json.dumps(function_result) # Ensure output is a JSON string
                    })

                # Make a second call to the sub-bot with the function results
                print(f"DEBUG: Sending function results back to Sub-Bot '{bot_name}' (Prev ID: {response.id})")
                second_response = client.responses.create(
                    model=self.model,
                    instructions=personalized_instructions,
                    previous_response_id=response.id, # Link conversation
                    input=function_results,
                    tools=tools # Sub-bot might still need tools definition
                )
                print(f"DEBUG: Sub-Bot '{bot_name}' Second Response ID: {second_response.id}")
                return second_response # Return the final response after function execution

            else:
                # If no function calls, return the first response
                print(f"DEBUG: Sub-Bot '{bot_name}' did not request function calls.")
                return response

        except Exception as e:
            print(f"Error processing with sub-bot '{bot_name}': {e}")
            traceback.print_exc()
            return {"error": str(e), "bot_name": bot_name} # Return error dict

    def _send_delegation_status(self, channel_id, user_id, bot_name, status):
        """
        Send or remove an ephemeral message indicating delegation status.
        
        Args:
            channel_id: Channel ID where the conversation is happening
            user_id: Slack ID of the user
            bot_name: Name of the delegate bot
            status: Either "start" or "end"
        """
        if not self.slack_client:
            print("DEBUG: No slack_client available, skipping ephemeral message")
            return
            
        # Create a unique key for this channel/user combination
        key = f"{channel_id}_{user_id}"
        
        try:
            if status == "start":
                # Send an ephemeral message
                delegate_names = {
                    "pto_wfh": "PTO/WFH",
                    "targetprocess": "TargetProcess",
                    "users": "Users",
                    "admin": "Admin"
                }
                bot_display_name = delegate_names.get(bot_name, bot_name.capitalize())
                
                response = self.slack_client.chat_postEphemeral(
                    channel=channel_id,
                    user=user_id,
                    text=f":hourglass_flowing_sand: Requesting help from the {bot_display_name} delegate bot..."
                )
                
                # Store the timestamp for later reference
                if response and response["ok"]:
                    print(f"DEBUG: Sent ephemeral message for {bot_display_name} delegation")
                    self.ephemeral_msg_timestamps[key] = response["message_ts"]
            
            elif status == "end":
                # We can't delete ephemeral messages through the API, they'll 
                # automatically disappear once replaced with the real response
                # We just remove our reference to it
                if key in self.ephemeral_msg_timestamps:
                    print(f"DEBUG: Removed ephemeral message reference for {key}")
                    del self.ephemeral_msg_timestamps[key]
                    
        except Exception as e:
            # Log error but don't interrupt the main flow
            print(f"Error managing delegation status message: {e}")
            traceback.print_exc()

# Create a singleton instance for use throughout the application
bot_manager = BotManager() 