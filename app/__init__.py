import os
import base64
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
from pymongo import MongoClient
from folium import Choropleth
from dotenv import load_dotenv
from datetime import timedelta
from flask_bcrypt import Bcrypt
from pymongo.errors import DuplicateKeyError
from flask import (
    Flask,
    request,
    jsonify,
    send_file,
    Blueprint,
)
from flask_jwt_extended import JWTManager
from itsdangerous import URLSafeTimedSerializer

# Import Blueprints
from .basecamp.routes import routes, basecamp_bp
from .dashboard.routes import routes, dashboard_bp
from .toyota.routes import routes, toyota_bp
from .heatmap.routes import routes, heatmap_bp
from .subdomain.routes import routes, subdomain_bp
from .transcription.routes import routes, transcript_bp


def create_app():
    # Create Flask app instance
    app = Flask(__name__)

    load_dotenv()
    CORS(
        app,
        resources={r"/*": {"origins": os.getenv("base_url_react")}},
        supports_credentials=True,
    )

    # LT Web Tool Dashboard Project
    def generate_jwt_secret_key(length=64):
        # Generate random bytes
        random_bytes = os.urandom(length)
        # Base64 encode the bytes to create a URL-safe secret key
        secret_key = base64.urlsafe_b64encode(random_bytes).decode("utf-8")
        return secret_key

    app.config["TOKEN_KEY"] = os.getenv("TOKEN_KEY")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)
    app.config["JWT_SECRET_KEY"] = generate_jwt_secret_key()

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

    # Random CJ Request Project
    @app.route("/ai_prompt_download")
    def download_file():
        file_path = "./ai_prompt_download/AI_Prompt_Files.zip"
        return send_file(file_path, as_attachment=True)

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
            model="gpt-4-1106-preview",
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

    @app.route("/")
    def index():
        return {"STATUS": "SUCCESS", "CODE": 200}

    return app
