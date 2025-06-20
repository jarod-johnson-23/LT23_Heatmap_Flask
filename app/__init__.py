import os
from dotenv import load_dotenv

load_dotenv()

import base64
from io import BytesIO
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
from pymongo import MongoClient
from folium import Choropleth
from datetime import timedelta
from flask_bcrypt import Bcrypt
from flask_socketio import SocketIO
import zipfile
from pymongo.errors import DuplicateKeyError
from flask_socketio import SocketIO, emit, join_room
from flask import (
    Flask,
    request,
    jsonify,
    url_for,
    send_file,
    Blueprint,
)
from flask_jwt_extended import JWTManager
from itsdangerous import URLSafeTimedSerializer
from werkzeug.utils import secure_filename


# Import Blueprints
from .basecamp.routes import routes, basecamp_bp
from .dashboard.routes import routes, dashboard_bp
from .toyota.routes import routes, toyota_bp
from .heatmap.routes import routes, heatmap_bp
from .subdomain.routes import routes, subdomain_bp
from .transcription.routes import routes, transcript_bp
from .targetprocess.routes import routes, targetprocess_bp
from .assistants.routes import routes, assistants_bp, setup_socketio
from .audiobot.routes import routes, audiobot_bp
from .slackbot.routes import routes, slackbot_bp
from .cocopah.routes import cocopah_bp

socketio = SocketIO()

def create_app():
    # Create Flask app instance
    app = Flask(__name__)

    CORS(
        app,
        resources={r"/*": {"origins": "*"}},
        supports_credentials=True,
    )

    # Initialize socketio
    socketio.init_app(app, cors_allowed_origins=os.getenv("base_url_react"), manage_session=False)
    setup_socketio(socketio)

    # LT Web Tool Dashboard Project
    def generate_jwt_secret_key(length=64):
        # Generate random bytes
        random_bytes = os.urandom(length)
        # Base64 encode the bytes to create a URL-safe secret key
        secret_key = base64.urlsafe_b64encode(random_bytes).decode("utf-8")
        return secret_key
    
    UPLOAD_FOLDER = "./uploads"
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config["TOKEN_KEY"] = os.getenv("TOKEN_KEY")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)
    jwt_secret_key = generate_jwt_secret_key()
    app.config["JWT_SECRET_KEY"] = jwt_secret_key
    app.secret_key = jwt_secret_key  # Set the secret_key for Flask session

    app.bcrypt = Bcrypt(app)
    app.jwt = JWTManager(app)
    app.serializer = URLSafeTimedSerializer(app.config["TOKEN_KEY"])

    # Register Blueprints
    app.register_blueprint(basecamp_bp, url_prefix="/basecamp")
    app.register_blueprint(dashboard_bp, url_prefix="/users")
    app.register_blueprint(toyota_bp, url_prefix="/toyota")
    app.register_blueprint(heatmap_bp, url_prefix="/heatmap")
    app.register_blueprint(subdomain_bp, url_prefix="/subdomain")
    app.register_blueprint(transcript_bp, url_prefix="/transcription")
    app.register_blueprint(targetprocess_bp, url_prefix="/targetprocess")
    app.register_blueprint(assistants_bp, url_prefix="/assistants")
    app.register_blueprint(audiobot_bp, url_prefix="/audiobot")
    app.register_blueprint(slackbot_bp, url_prefix="/slack")
    app.register_blueprint(cocopah_bp, url_prefix="/cocopah")
    # Configure Flask-PyMongo
    mongo_uri = os.getenv("mongo_uri")
    client = MongoClient(mongo_uri)
    app.db = client["LT-db-dashboard"]
    app.user_collection = app.db["userInfo"]
    app.user_collection.create_index("email", unique=True)
    app.subdomain_collection = app.db["subdomains"]
    app.subdomain_collection.create_index("subdomain", unique=True)
    app.timestamps_collection = app.db["timestamps"]
    app.timestamps_collection.create_index("timestamp", unique=True)
    app.project_sow_field_collection = app.db["lt_project_sow_fields"]
    app.project_sow_field_collection.create_index("project_num", unique=True)

    # Beau Joke Project
    @app.route("/joke/beau", methods=["POST"])
    def makeJoke():
        ai_client = OpenAI(
            organization=os.getenv("openai_organization"),
            api_key=os.getenv("openai_api_key"),
        )
        theme = request.json.get("theme")
        responses = request.json.get("responses")
        if not theme:
            theme = "anything"
        if not responses:
            responses = {
                "role": "system",
                "content": "There are no previous messages in the chat log, do not worry about creating duplicate jokes.",
            }
        response = ai_client.chat.completions.create(
            model="gpt-4o",
            temperature=1,
            messages=[
                {
                    "role": "system",
                    "content": """You are the President of the company LaneTerralever, 
                        or more commonly known as just 'LT'. You are about 60 years old 
                        and have 40 years of experience in marketing and business. You are 
                        also known for being funny but in a dad-joke way with lots of puns 
                        and plays on words. When you tell a joke, the usual response is 
                        'Wow' followed by a couple chuckles. People refer to you as Beau 
                        Lane but sometimes spell it wrong like 'Bo'. Users will come to you 
                        to give them a joke and it is your job to create a concise and funny 
                        dad joke. The maximum length of the joke should be 30 words, but 
                        on average, the jokes should be around 10-20 words. Do not use a joke 
                        that has already been used in the current chat logs. There is no need 
                        to state that you understand the request and you should only respond 
                        with the joke and nothing else.""",
                },
                responses,
                {
                    "role": "user",
                    "content": f"Please give me a dad joke about: {theme}",
                },
            ],
        )
        moderation = ai_client.moderations.create(
            input=str(response.choices[0].message)
        )
        if moderation.results[0].flagged:
            return jsonify({"error": "joke was deemed inappropriate"}), 304
        return str(response.choices[0].message.content)
    
    @app.route('/get-ip', methods=['GET'])
    def get_ip():
        if request.headers.getlist("X-Forwarded-For"):
            ip = request.headers.getlist("X-Forwarded-For")[0]
        else:
            ip = request.remote_addr
        return jsonify({'ip': ip})

    @app.route("/")
    def index():
        return {"STATUS": "SUCCESS", "CODE": 200}
    
    @app.route('/mailing-list-generation', methods=['POST'])
    def generate_mailing_list():
        # Retrieve special offer data
        offer_data = request.form.get('offerData')
        
        # Initialize the response dictionary
        response = {
            'offerData': offer_data,
            'files': {}
        }
        
        # Check and add file information to the response
        files_to_check = [
            'offerTable', 'activePatrons', 'wmyPatrons', 'wsmyPatrons', 'seasonalPatrons', 
            'patriotCardPatrons', 'claTierPatrons'
        ]
        
        for file_key in files_to_check:
            if file_key in request.files:
                file = request.files[file_key]
                response['files'][file_key] = {
                    'filename': file.filename,
                    'content_type': file.content_type,
                    'size': len(file.read())
                }
            else:
                response['files'][file_key] = 'No file uploaded'

        print(jsonify(response))
        
        # Return the response as JSON
        return jsonify(response)
    
    

    return app