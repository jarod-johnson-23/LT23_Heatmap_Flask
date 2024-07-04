from flask import Blueprint, session, request, send_file, jsonify
from flask_socketio import emit, join_room
from openai import OpenAI, AssistantEventHandler
import uuid
import requests
import os
import json
import mimetypes

assistants_bp = Blueprint("assistants_bp", __name__)
from . import routes

# Initialize OpenAI client
client = OpenAI(
    organization=os.getenv("openai_organization"),
    api_key=os.getenv("openai_api_key"),
)

ASSISTANTS_DIR = os.path.dirname(os.path.abspath(__file__))

# Use the provided assistant ID from the environment
assistant_id = os.getenv("SOW_ASSISTANT_ID")

@assistants_bp.before_app_request
def before_request():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
    if 'thread_id' not in session:
        thread = client.beta.threads.create()
        session['thread_id'] = thread.id  # Accessing the 'id' attribute correctly

@assistants_bp.route("/download_file", methods=['POST'])
def download_openai_file():
    # Get the filename from the request
    data = request.get_json()
    print(data)
    file_id = data.get('file_id')
    filename = data.get('filename')
    print("File ID: ",file_id)
    print("File Name: ",filename)
    
    if not filename:
        return jsonify({"error": "Filename is required"}), 400

    # Determine the file extension and type
    file_extension = filename.split('.')[-1]
    mime_type, _ = mimetypes.guess_type(filename)

    # Get the content of the file
    response = client.files.content(file_id)
    content = response.read()

    # Save the content to a temporary file
    temp_file_path = f"/tmp/{filename}_{file_id}"
    with open(temp_file_path, 'wb') as file:
        file.write(content)

    # Send the file to the client with the appropriate MIME type
    return send_file(temp_file_path, as_attachment=True, mimetype=mime_type, download_name=filename)

def setup_socketio(socketio):
    @socketio.on('connect', namespace='/assistants')
    def handle_connect(auth):
        user_id = session.get('user_id')
        if user_id is None:
            user_id = request.args.get('user_id')
            session['user_id'] = user_id
            thread = client.beta.threads.create()
            session['thread_id'] = thread.id

        join_room(user_id)
        emit('connected', {'message': 'You are connected.', 'user_id': user_id})

    @socketio.on('sow_chat', namespace='/assistants')
    def handle_message(data):
        user_id = session.get('user_id')
        print(user_id)
        thread_id = session.get('thread_id')

        if not thread_id:
            thread = client.beta.threads.create()
            thread_id = thread.id
            session['thread_id'] = thread_id

        prompt = data['prompt']

        # Add message to the thread
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=prompt
        )

        class EventHandler(AssistantEventHandler):
          def on_text_created(self, text):
              emit('new_message', {'message': text.value}, room=user_id, namespace='/assistants')

          def on_text_delta(self, delta, snapshot):
              if delta.value:
                  emit('new_message', {'message': delta.value}, room=user_id, namespace='/assistants')

          def on_event(self, event):
            if event.event == 'thread.run.requires_action':
                run_id = event.data.id
                self.handle_requires_action(event.data, run_id)
            elif event.event == 'thread.message.completed':
                if event.data.attachments:
                    for attachment in event.data.attachments:
                        file_id = attachment.file_id
                        # Extract filename from the content
                        filename = None
                        for content_block in event.data.content:
                            if content_block.text.annotations:
                                for annotation in content_block.text.annotations:
                                    if annotation.file_path.file_id == file_id:
                                        filename = annotation.text.split('/')[-1]
                                        break
                            if filename:
                                break
                        
                        if filename:
                            emit('file_created', {'filename': filename, 'file_id': file_id}, room=user_id, namespace='/assistants')
                        

          def handle_requires_action(self, data, run_id):
              tool_outputs = []
              print("Handle Requires Action was called")
              for tool in data.required_action.submit_tool_outputs.tool_calls:
                  print(tool)
                  if tool.function.name == "TargetProcess_Project_Data":
                      arguments = tool.function.arguments
                      try:
                          arguments_dict = json.loads(arguments)
                          sow_file_name = arguments_dict.get("sow_file_name")
                          print(f"Calling custom function with {sow_file_name}")
                          base_url = os.getenv("base_url_flask")
                          api_key = os.getenv("TP_VALIDATION_KEY")
                          response = requests.get(f"{base_url}/targetprocess/userstories/sow/{sow_file_name}",
                                                  headers={"X-API-KEY": api_key})
                          if response.status_code == 200:
                            response_data = response.json()
                            response_str = json.dumps(response_data)  # Convert JSON response to string
                            print(response_str)
                            print(f"Function call successful: {response_str}")
                            tool_outputs.append({"tool_call_id": tool.id, "output": response_str})
                          else:
                            print(f"Function call failed: {response.text}")
                            tool_outputs.append({"tool_call_id": tool.id, "output": response.text})
                      except json.JSONDecodeError as e:
                          print(f"Error decoding JSON arguments: {e}")
                          tool_outputs.append({"tool_call_id": tool.id, "output": str(e)})
                      except Exception as e:
                          print(f"Error in handle_requires_action: {e}")
                          tool_outputs.append({"tool_call_id": tool.id, "output": str(e)})
              
              self.submit_tool_outputs(tool_outputs, run_id)


          def submit_tool_outputs(self, tool_outputs, run_id):
              with client.beta.threads.runs.submit_tool_outputs_stream(
                  thread_id=self.current_run.thread_id,
                  run_id=self.current_run.id,
                  tool_outputs=tool_outputs,
                  event_handler=EventHandler(),
              ) as stream:
                  for text in stream.text_deltas:
                      print(text, end="", flush=True)
                  print()

          def on_end(self):
              emit('message_done', room=user_id, namespace='/assistants')

        # Run the thread and stream the response
        with client.beta.threads.runs.stream(
            thread_id=thread_id,
            assistant_id=assistant_id,
            event_handler=EventHandler(),
        ) as stream:
            stream.until_done()