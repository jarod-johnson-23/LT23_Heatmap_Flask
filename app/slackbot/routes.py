from flask import Blueprint, current_app, request, jsonify, send_from_directory
import os
import json
import boto3
from botocore.exceptions import ClientError
import re
import pytz
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from openai import OpenAI
from datetime import datetime, time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pathlib import Path
from .database import (
    init_db, get_previous_response_id, update_response_id, reset_all_conversations,
    is_user_authenticated, extract_email, generate_verification_code, 
    store_verification_code, verify_code, get_user_email, cleanup_old_processed_messages,
    is_message_processed, mark_message_processed
)
import requests
import xml.etree.ElementTree as ET
from .potenza import execute_sql_query
from .bot_manager import bot_manager

slackbot_bp = Blueprint("slackbot_bp", __name__)

from . import routes

# Define paths for bot configuration files
BOT_DIR = Path(__file__).parent / "bot"
INSTRUCTIONS_PATH = BOT_DIR / "instructions.txt"
TOOLS_PATH = BOT_DIR / "tools.json"

# Create the bot directory if it doesn't exist
BOT_DIR.mkdir(parents=True, exist_ok=True)

# Create default files if they don't exist
if not INSTRUCTIONS_PATH.exists():
    with open(INSTRUCTIONS_PATH, 'w') as f:
        f.write("You are a helpful assistant integrated with Slack.")

if not TOOLS_PATH.exists():
    with open(TOOLS_PATH, 'w') as f:
        f.write("[]")

def load_bot_instructions():
    """Load the bot instructions from the text file."""
    try:
        with open(INSTRUCTIONS_PATH, 'r') as f:
            instructions = f.read().strip()
            
            # Add current date information
            current_date = datetime.now().strftime("%Y-%m-%d")
            instructions = f"{instructions}\n\nToday's date is {current_date}."
            
            return instructions
    except Exception as e:
        print(f"Error loading bot instructions: {e}")
        current_date = datetime.now().strftime("%Y-%m-%d")
        return f"You are a helpful assistant integrated with Slack. Today's date is {current_date}."

def load_bot_tools():
    """Load the bot tools from the JSON file."""
    try:
        with open(TOOLS_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading bot tools: {e}")
        return []

# Initialize OpenAI client
client = OpenAI(
    organization=os.getenv("openai_organization"),
    api_key=os.getenv("openai_api_key"),
)

# Initialize Slack client
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# Initialize the database
init_db()

# Set up the scheduler for daily reset at 2AM MST (9AM UTC)
# Explicitly set timezone to UTC to avoid server timezone issues
scheduler = BackgroundScheduler(timezone='UTC')
mst_timezone = pytz.timezone('America/Denver')  # MST timezone

def daily_reset_job():
    """Reset all conversations daily at 2AM MST (9AM UTC)."""
    reset_count = reset_all_conversations()
    deleted_count = cleanup_old_processed_messages()
    current_time_utc = datetime.now(pytz.UTC)
    current_time_mst = current_time_utc.astimezone(mst_timezone)
    print(f"[{current_time_mst}] Daily reset: Cleared {reset_count} conversation histories and {deleted_count} processed message records")

# Schedule the job to run at 9AM UTC (2AM MST)
scheduler.add_job(
    daily_reset_job,
    trigger=CronTrigger(hour=9, minute=0, timezone=pytz.UTC),
    id='daily_reset_job',
    name='Reset all conversations at 9AM UTC (2AM MST)',
    replace_existing=True
)

scheduler.start()

def send_verification_email(recipient_email, code):
    """Send a verification code email using AWS SES, matching the dashboard pattern."""
    SOURCE_EMAIL = "no-reply@laneterraleverapi.org" # Verified source email address
    AWS_REGION = "us-east-2"  # Matching the dashboard example region
    SUBJECT = "Your LT-AI Slackbot Verification Code"
    BODY_TEXT = (f"Hello,\n\n"
                 f"Your verification code for the LT-AI Slackbot is: {code}\n\n"
                 f"Please enter this code in your Slack chat with the bot to complete authentication.\n"
                 f"If you did not request this code, please ignore this email.")
    CHARSET = "UTF-8"

    try:
        client = boto3.client(
            "ses",
            region_name=AWS_REGION,
            aws_access_key_id=os.getenv("aws_access_key_id"),
            aws_secret_access_key=os.getenv("aws_secret_access_key")
        )
    except Exception as e:
         print(f"Error creating boto3 client: {e}")
         return False

    try:
        # Provide the contents of the email, using only the Text body like the dashboard example
        response = client.send_email(
            Destination={
                'ToAddresses': [
                    recipient_email,
                ],
            },
            Message={
                'Body': {
                    # Only including Text part to match the dashboard pattern
                    'Text': {
                        'Charset': CHARSET,
                        'Data': BODY_TEXT,
                    },
                    # Removed Html part
                },
                'Subject': {
                    'Charset': CHARSET,
                    'Data': SUBJECT,
                },
            },
            Source=SOURCE_EMAIL, # Use the verified source email address directly here
            # ReplyToAddresses=[SENDER], # Optional: If you want replies to go somewhere specific
        )
    # Display an error if something goes wrong.
    except ClientError as e:
        print(f"Email sending failed: {e.response['Error']['Message']}")
        return False
    except Exception as e: # Catch other potential errors (e.g., client creation failed)
         print(f"An unexpected error occurred during email sending: {e}")
         return False
    else:
        print(f"Email sent! Message ID: {response['MessageId']}")
        return True

@slackbot_bp.route("/events", methods=["POST"])
def slack_events():
    data = request.json
    
    # Handle URL verification challenge from Slack
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge")})
    
    # Handle events
    if data.get("event"):
        event = data.get("event")
        
        # Handle message events
        if event.get("type") == "message" and not event.get("bot_id"):
            channel_id = event.get("channel")
            user_id = event.get("user")
            text = event.get("text", "").strip()
            channel_type = event.get("channel_type")
            message_ts = event.get("ts", "")  # Get the message timestamp
            
            # Check if this message has already been processed
            if is_message_processed(message_ts, channel_id):
                print(f"Skipping already processed message: {message_ts} in channel {channel_id}")
                return jsonify({"status": "ok"})
            
            # Only process messages in direct messages (DMs)
            if channel_type == "im":
                # Mark this message as processed to prevent duplicate responses
                mark_message_processed(message_ts, channel_id)
                
                # Check if the user is authenticated
                if is_user_authenticated(user_id):
                    # User is authenticated, proceed with normal conversation
                    user_email = get_user_email(user_id)
                    print(f"Authenticated user {user_id} ({user_email}) sent: {text}")
                    
                    # Get the previous response ID for this channel
                    previous_response_id = get_previous_response_id(channel_id)
                    
                    try:
                        # Initialize the bot manager with the Slack client
                        bot_manager.slack_client = slack_client

                        # Process the message using the bot manager
                        response_obj = bot_manager.process_message(
                            text, 
                            user_email, 
                            user_id, 
                            previous_response_id, 
                            channel_id
                        )

                        # Update the response ID using the final response object's ID
                        if hasattr(response_obj, 'id'):
                            update_response_id(channel_id, response_obj.id)
                        elif isinstance(response_obj, dict) and 'id' in response_obj: # Handle potential error dict with ID
                             update_response_id(channel_id, response_obj['id'])

                        # Extract the final text response from the response object
                        bot_response_text = ""
                        if hasattr(response_obj, 'output'):
                             for output_item in response_obj.output:
                                 if hasattr(output_item, 'content') and output_item.content:
                                     for content_item in output_item.content:
                                         if hasattr(content_item, 'text') and content_item.text:
                                             bot_response_text += content_item.text
                        elif isinstance(response_obj, dict) and 'error' in response_obj:
                             # Handle cases where process_message returned an error dict directly
                             bot_response_text = response_obj.get('output', [{}])[0].get('content', [{}])[0].get('text', f"An error occurred: {response_obj['error']}")

                        # Send the response back to Slack
                        if bot_response_text:
                            slack_client.chat_postMessage(
                                channel=channel_id,
                                text=bot_response_text
                            )
                        else:
                            # Handle cases where no text response was generated
                            print("Warning: No text response generated by bot manager.")
                            slack_client.chat_postMessage(
                                channel=channel_id,
                                text="I received your message, but I couldn't generate a text response."
                            )

                    except Exception as e:
                        print(f"Error in Slack event handler: {e}")
                        import traceback
                        traceback.print_exc()
                        try:
                            slack_client.chat_postMessage(
                                channel=channel_id,
                                text=f"Sorry, I encountered an unexpected error while processing your request."
                            )
                        except Exception as slack_e:
                             print(f"Failed to send error message to Slack: {slack_e}")
                else:
                    # User is not authenticated, handle authentication flow
                    # Check if the message contains a verification code (6 digits)
                    code_match = re.match(r'^\s*(\d{6})\s*$', text)
                    if code_match:
                        # User sent a verification code
                        code = code_match.group(1)
                        success, result = verify_code(user_id, code)
                        
                        if success:
                            # Code is valid, user is now authenticated
                            slack_client.chat_postMessage(
                                channel=channel_id,
                                text=f"Thank you! You've been successfully authenticated as {result}. How can I help you today?"
                            )
                        else:
                            # Code is invalid
                            slack_client.chat_postMessage(
                                channel=channel_id,
                                text=f"Sorry, {result}. Please try again or provide your email address to receive a new code."
                            )
                    else:
                        # Check if the message contains an email address
                        email = extract_email(text)
                        if email:
                            # User sent an email address, send verification code
                            code = generate_verification_code()
                            store_verification_code(user_id, email, code)
                            
                            if send_verification_email(email, code):
                                slack_client.chat_postMessage(
                                    channel=channel_id,
                                    text=f"I've sent a verification code to {email}. Please enter the 6-digit code to complete authentication."
                                )
                            else:
                                slack_client.chat_postMessage(
                                    channel=channel_id,
                                    text=f"I couldn't send an email to {email}. Please check the email address and try again."
                                )
                        else:
                            # User sent something else, prompt for email
                            slack_client.chat_postMessage(
                                channel=channel_id,
                                text="Hello! I don't think we have met before. I need to verify you are who you say you are before we can continue. Please provide your email address and I will send you a quick verification code."
                            )
    
    return jsonify({"status": "ok"})

@slackbot_bp.route("/update-instructions", methods=["POST"])
def update_instructions():
    """Update the bot instructions."""
    try:
        new_instructions = request.json.get("instructions")
        if not new_instructions:
            return jsonify({"status": "error", "message": "No instructions provided"}), 400
        
        with open(INSTRUCTIONS_PATH, 'w') as f:
            f.write(new_instructions)
        
        return jsonify({"status": "success", "message": "Bot instructions updated successfully"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@slackbot_bp.route("/update-tools", methods=["POST"])
def update_tools():
    """Update the bot tools."""
    try:
        new_tools = request.json.get("tools")
        if new_tools is None:
            return jsonify({"status": "error", "message": "No tools provided"}), 400
        
        with open(TOOLS_PATH, 'w') as f:
            json.dump(new_tools, f, indent=2)
        
        return jsonify({"status": "success", "message": "Bot tools updated successfully"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@slackbot_bp.route("/get-config", methods=["GET"])
def get_config():
    """Get the current bot configuration."""
    try:
        instructions = load_bot_instructions()
        tools = load_bot_tools()
        
        return jsonify({
            "instructions": instructions,
            "tools": tools
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@slackbot_bp.route("/install", methods=["GET"])
def slack_install():
    # This would be for a Slack app installation flow
    # For now, just return a simple message
    return "Slack app installation page"

@slackbot_bp.route("/reset/<channel_id>", methods=["POST"])
def reset_conversation(channel_id):
    """Reset the conversation history for a specific channel."""
    try:
        update_response_id(channel_id, None)
        return jsonify({"status": "success", "message": f"Conversation reset for channel {channel_id}"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@slackbot_bp.route("/reset-all", methods=["POST"])
def reset_all():
    """Manually trigger reset of all conversations."""
    try:
        reset_count = reset_all_conversations()
        return jsonify({
            "status": "success", 
            "message": f"Reset {reset_count} conversations"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

