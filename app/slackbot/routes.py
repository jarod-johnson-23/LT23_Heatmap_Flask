from flask import Blueprint, current_app, request, jsonify, send_from_directory
import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from openai import OpenAI
import re
import random
from .database import init_db, get_previous_response_id, update_response_id, cleanup_expired_conversations

slackbot_bp = Blueprint("slackbot_bp", __name__)

# Initialize OpenAI client
client = OpenAI(
    organization=os.getenv("openai_organization"),
    api_key=os.getenv("openai_api_key"),
)

# Initialize Slack client
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# Initialize the database
init_db()

@slackbot_bp.route("/events", methods=["POST"])
def slack_events():
    data = request.json
    
    # Handle URL verification challenge from Slack
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge")})
    
    # Occasionally clean up expired conversations (1% chance per request)
    if random.random() < 0.01:
        cleanup_expired_conversations()
    
    # Handle events
    if data.get("event"):
        event = data.get("event")
        
        # Handle message events
        if event.get("type") == "message" and not event.get("bot_id"):
            channel_id = event.get("channel")
            user_id = event.get("user")
            text = event.get("text", "")
            
            # Extract direct mentions to the bot
            direct_mention = re.search(r"<@(\w+)>", text)
            bot_user_id = os.getenv("SLACK_BOT_USER_ID")
            
            # Process the message if it's a direct mention to our bot or in a DM
            if (direct_mention and direct_mention.group(1) == bot_user_id) or event.get("channel_type") == "im":
                # Remove the mention from the text if it exists
                if direct_mention:
                    text = re.sub(r"<@\w+>", "", text).strip()
                
                # Get the previous response ID for this channel
                previous_response_id = get_previous_response_id(channel_id)
                
                # Get response from OpenAI
                try:
                    response = client.responses.create(
                        model="gpt-4o-mini",
                        instructions="You are a helpful assistant integrated with Slack.",
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

@slackbot_bp.route("/cleanup", methods=["POST"])
def cleanup():
    """Manually trigger cleanup of expired conversations."""
    try:
        deleted_count = cleanup_expired_conversations()
        return jsonify({
            "status": "success", 
            "message": f"Cleaned up {deleted_count} expired conversations"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

