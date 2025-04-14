from flask import Blueprint, current_app, request, jsonify, send_from_directory
import os
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
from .database import (
    init_db, get_previous_response_id, update_response_id, reset_all_conversations,
    is_user_authenticated, extract_email, generate_verification_code, 
    store_verification_code, verify_code, get_user_email, cleanup_old_processed_messages,
    is_message_processed, mark_message_processed
)

slackbot_bp = Blueprint("slackbot_bp", __name__)

from . import routes

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

# Start the scheduler
scheduler.start()

def send_verification_email(email, code):
    """Send a verification email with the one-time code."""
    aws_region = "us-east-2"
    
    # Create a new SES resource and specify a region
    client = boto3.client(
        "ses",
        region_name=aws_region,
        aws_access_key_id=os.getenv("aws_access_key_id"),
        aws_secret_access_key=os.getenv("aws_secret_access_key"),
    )
    
    # Email content with the verification code
    email_body = f"""
    Hello,
    
    You've requested to authenticate with the LT Slack Bot. Your verification code is:
    
    {code}
    
    This code will expire in 10 minutes.
    
    If you didn't request this code, please ignore this email.
    """
    
    try:
        # Send the email
        response = client.send_email(
            Destination={
                "ToAddresses": [email],
            },
            Message={
                "Body": {
                    "Text": {
                        "Charset": "UTF-8",
                        "Data": email_body,
                    },
                },
                "Subject": {
                    "Charset": "UTF-8",
                    "Data": "LT Slack Bot Verification Code",
                },
            },
            Source="no-reply@laneterraleverapi.org",  # Your verified address
        )
        print(f"Email sent! Message ID: {response['MessageId']}")
        return True
    except ClientError as e:
        print(f"An error occurred: {e.response['Error']['Message']}")
        return False

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
                    
                    # Get response from OpenAI
                    try:
                        response = client.responses.create(
                            model="gpt-4o-mini",
                            instructions=f"You are a helpful assistant integrated with Slack. You are chatting with {user_email}.",
                            previous_response_id=previous_response_id,
                            input=[
                                {"role": "user", "content": text}
                            ]
                        )
                        
                        # Store the new response ID for future conversations
                        update_response_id(channel_id, response.id)
                        
                        # Send the response back to Slack
                        bot_response = response.output[0].content[0].text
                        slack_client.chat_postMessage(
                            channel=channel_id,
                            text=bot_response
                        )
                    except Exception as e:
                        print(f"Error: {e}")
                        slack_client.chat_postMessage(
                            channel=channel_id,
                            text=f"Sorry, I encountered an error: {str(e)}"
                        )
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

