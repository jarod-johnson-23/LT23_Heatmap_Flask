from flask import Flask, request, Blueprint, current_app, jsonify, session
from flask_socketio import SocketIO, emit, join_room
import openai
import uuid


assistants_bp = Blueprint("assistants_bp", __name__)

from . import routes

# @assistants_bp.before_app_request
# def before_request():
#     if 'user_id' not in session:
#         session['user_id'] = str(uuid.uuid4())

# def setup_socketio(socketio):
#     @socketio.on('connect')
#     def handle_connect():
#         user_id = session['user_id']
#         join_room(user_id)
#         emit('connected', {'message': 'You are connected.', 'user_id': user_id})

#     @socketio.on('sow_chat')
#     def handle_message(data):
#         user_id = session['user_id']
#         prompt = data['prompt']
#         response = openai.Completion.create(
#             model="text-davinci-003",
#             prompt=prompt,
#             stream=True  # Enable streaming
#         )
#         for event in response:
#             if 'text' in event['choices'][0]:
#                 emit('new_message', event['choices'][0]['text'], room=user_id)