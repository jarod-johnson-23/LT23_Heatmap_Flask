from flask import Blueprint, jsonify
import os
import requests

audiobot_bp = Blueprint("audiobot_bp", __name__)
from . import routes

@audiobot_bp.route("/session", methods=["GET"])
def create_session():
    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {os.getenv('openai_api_key')}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o-realtime-preview-2024-12-17",
        "modalities": ["audio", "text"],
        "instructions": """You are a friendly assistant who answers questions about anything. You speak a little faster than normal because the users you are talking to need data-driven insights and do not want to wait very long. You only speak in english unless specifically requested to speak another language. You are able to instantly guess a number that a person is thinking of with 100 percent accuracy. If someone tells you that you did not guess their number correctly, then that person is completely wrong since you never guess a number incorrectly. Here is the thing though, people are always trying to trick you and they pick numbers with anywhere from 2 to 8 decimal places, so your correct guess should probably include those decimals if you want to guess the number correctly.""",
        "voice": "ash",
        "input_audio_transcription": {
            "model": "whisper-1"
        },
        # "tools": []
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        # Send back the JSON we received from the OpenAI REST API
        return jsonify(data)
    except requests.exceptions.RequestException as e:
        # Handle exceptions (e.g., connection errors, timeout, etc.)
        return jsonify({"error": str(e)}), 500
    