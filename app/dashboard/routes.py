import os
import base64
import boto3
from botocore.exceptions import ClientError
from bson.objectid import ObjectId
from flask_bcrypt import Bcrypt
from pymongo.errors import DuplicateKeyError
from flask import Flask, request, Blueprint, current_app, jsonify
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity,
)
from itsdangerous import SignatureExpired, BadSignature, URLSafeTimedSerializer

dashboard_bp = Blueprint("dashboard_bp", __name__)

from . import routes


# LT Web Tool Dashboard Project
@dashboard_bp.route("/admin/create-user", methods=["POST"])
def admin_create_user():
    email = request.json.get("email")
    access = request.json.get("accessRights")  # Presuming the admin sends this

    # Check if email was provided
    if not email:
        return jsonify({"error": "Email is required"}), 400

    try:
        user = {
            "email": email,
            "access": access,
            "setupComplete": False,  # Indicates the user has not completed the setup
        }

        # Insert the user into the database
        result = current_app.user_collection.insert_one(user)

        token = current_app.serializer.dumps(email, salt=os.getenv("salt"))

        # Create a link to the account creation page with the token
        link = f"{os.getenv('base_url_react')}/create-account/{token}"

        # Email content with the link
        email_body = f"""To complete your sign up and gain access, simply click on the following link and follow the instructions to create your account: {link}\nPlease note that this invitation link is uniquely tied to your email address, sharing it with others will result in an account under your email address. If you need to use a different email address, please contact the DEV team. The invitation link will expire in 10 hours."""

        aws_region = "us-east-2"

        # Create a new SES resource and specify a region.
        client = boto3.client(
            "ses",
            region_name=aws_region,
            aws_access_key_id=os.getenv("aws_access_key_id"),
            aws_secret_access_key=os.getenv("aws_secret_access_key"),
        )

        try:
            # Provide the contents of the email.
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
                        "Data": "LT Web Service Dashboard Invitation",
                    },
                },
                Source="no-reply@laneterraleverapi.org",  # Your verified address
            )
        except ClientError as e:
            print(f"An error occurred: {e.response['Error']['Message']}")
        else:
            print(f"Email sent! Message ID: {response['MessageId']}")
        user_id = result.inserted_id

        # Return the ObjectId as a string in the response
        # Since ObjectId is not JSON serializable, we convert it to string
        return jsonify({"msg": link, "_id": str(user_id)}), 201

    except DuplicateKeyError:
        return jsonify({"error": "Duplicate email"}), 409
    except Exception as e:
        print(e)
        return jsonify({"error": str(e)}), 400


# LT Web Tool Dashboard Project
@dashboard_bp.route("/register", methods=["POST"])
def user_complete_setup():
    email = request.json.get("email")
    password = request.json.get("password")
    first_name = request.json.get("firstName", "")
    last_name = request.json.get("lastName", "")
    role = request.json.get("role", "")

    # Validate that the required email and password have been provided
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    try:
        hashed_password = current_app.bcrypt.generate_password_hash(password).decode(
            "utf-8"
        )

        update_result = current_app.user_collection.update_one(
            {"email": email, "setupComplete": False},
            {
                "$set": {
                    "password": hashed_password,
                    "firstName": first_name,
                    "lastName": last_name,
                    "role": role,
                    "setupComplete": True,  # Mark the setup as complete
                }
            },
        )

        if update_result.matched_count == 0:
            return jsonify({"error": "No user found or setup already complete"}), 404

        access_token = create_access_token(identity=email)
        return jsonify({"msg": "Account setup complete", "token": access_token}), 200

    except DuplicateKeyError:
        return jsonify({"error": "Duplicate email"}), 409
    except Exception as e:
        print(e)
        return jsonify({"error": str(e)}), 400


# LT Web Tool Dashboard Project
@dashboard_bp.route("/login", methods=["POST"])
def login():
    email = request.json.get("email")
    password = request.json.get("password")
    if not email:
        return jsonify({"error": "Email is required"}), 400
    if not password:
        return jsonify({"error": "Password is required"}), 400
    user = current_app.user_collection.find_one({"email": email})
    if user and current_app.bcrypt.check_password_hash(user["password"], password):
        access_token = create_access_token(identity=email)
        return jsonify(access_token=access_token, id=str(user["_id"])), 200
    else:
        return jsonify({"msg": "Bad email or password"}), 401


# LT Web Tool Dashboard Project
@dashboard_bp.route("/get_access", methods=["POST"])
def get_access():
    data = request.json
    email = data.get("email")

    if not email:
        return jsonify({"message": "Email is required."}), 400

    # Query the database
    result = current_app.user_collection.find_one(
        {"email": email}, {"access": 1, "_id": 0}
    )

    # Check if a result was found
    if result:
        return jsonify(result), 200
    else:
        return jsonify({"message": "No user found with that email."}), 404


# LT Web Tool Dashboard Project
@dashboard_bp.route("/verify-token/<token>", methods=["GET"])
def verify_token(token):
    try:
        email = current_app.serializer.loads(
            token,
            salt=os.getenv("salt"),
            max_age=36000,  # Token expires after 10 hours
        )
    except SignatureExpired:
        return jsonify({"error": "Token expired"}), 400
    except BadSignature:
        return jsonify({"error": "Invalid token"}), 400

    # Token is valid, continue with the account creation process
    return jsonify({"message": "Token is valid", "email": email}), 200


# LT Web Tool Dashboard Project
@dashboard_bp.route("/", methods=["GET"])
def get_all_users():
    try:
        # Query all user documents excluding the "password" field
        users = current_app.user_collection.find({}, {"password": 0})
        user_list = list(users)

        # Convert the ObjectId fields to strings to make them JSON serializable
        for user in user_list:
            user["_id"] = str(user["_id"])

        return jsonify(user_list), 200
    except Exception as e:
        print(e)
        return jsonify({"error": "An error occurred fetching the user data."}), 500


# LT Web Tool Dashboard Project
@dashboard_bp.route("/<user_id>/update-access", methods=["PATCH"])
def update_user_access(user_id):
    try:
        access_rights = request.json.get("access")
        result = current_app.user_collection.update_one(
            {"_id": ObjectId(user_id)}, {"$set": {"access": access_rights}}
        )

        if result.matched_count == 0:
            return jsonify({"error": "No user found with provided ID."}), 404
        elif result.modified_count == 0:
            return jsonify({"error": "User access rights not updated."}), 304
        else:
            return jsonify({"message": "User access rights updated successfully."}), 200
    except Exception as e:
        print(e)
        return (
            jsonify({"error": "An error occurred updating the user access rights."}),
            500,
        )


# LT Web Tool Dashboard Project
@dashboard_bp.route("/<user_id>/delete", methods=["DELETE"])
def delete_user(user_id):
    try:
        result = current_app.user_collection.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count == 0:
            return jsonify({"error": "User not found."}), 404
        else:
            return jsonify({"message": "User deleted successfully."}), 200
    except Exception as e:
        print(e)
        return (
            jsonify({"error": "An error occurred while trying to delete the user."}),
            500,
        )


# LT Web Tool Dashboard Project
@dashboard_bp.route("/protected", methods=["GET"])
@jwt_required()
def protected():
    current_user = get_jwt_identity()
    return jsonify({"logged_in_as": current_user}), 200
