from flask import Blueprint, current_app, request, jsonify
from flask_jwt_extended import jwt_required
import os
from pymongo.errors import PyMongoError, DuplicateKeyError
import boto3
from botocore.exceptions import ClientError
import validators
import re

subdomain_bp = Blueprint("subdomain_bp", __name__)

from . import routes

INSTALL_ID = os.getenv("wp_engine_install_id")


@subdomain_bp.route("/delete_subdomain", methods=["DELETE"])
@jwt_required()
def delete_subdomain():
    # Extract subdomain and domain from request
    subdomain = request.json.get("subdomain_name")
    redirect_link = request.json.get("redirect_url")
    requester_email = request.json.get("email")
    domain = "lt.agency"

    # Check for required fields
    if not subdomain or not domain:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        # Attempt to delete the document from the subdomain_collection
        result = current_app.subdomain_collection.delete_one(
            {"subdomain": subdomain, "domain": domain}
        )

        # If the document does not exist or delete count is 0, return 404 not found
        if result.deleted_count == 0:
            return jsonify({"error": "Subdomain not found or already deleted"}), 404

        
        aws_region = "us-east-2"
        email_body = f"The user {requester_email} requested to delete the subdomain {subdomain}.lt.agency\n\nThis subdomain was redirected to {redirect_link}\n\nPlease fulfill this request within the next 48 hours."

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
                    "ToAddresses": ["jarod.johnson@lt.agency"],
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
                        "Data": "LT URL Redirect DELETE Request",
                    },
                },
                Source="no-reply@laneterraleverapi.org",
            )

        except ClientError as e:
            print(f"An error occurred: {e.response['Error']['Message']}")
        else:
            print(f"Email sent! Message ID: {response['MessageId']}")

        # If delete count is greater than 0, it means the operation was successful
        return jsonify({"message": "Subdomain deleted successfully"}), 200

    except PyMongoError as e:
        # Handle any other database exceptions
        return jsonify({"error": "Failed to delete subdomain", "details": str(e)}), 500

def is_valid_url(url):
    if validators.url(url):
        return True
    else:
        return False

def is_valid_subdomain(subdomain):
    pattern = r"^(?![0-9]+$)(?!-)[a-zA-Z0-9-]{1,63}(?<!-)$"
    if re.match(pattern, subdomain):
        return True
    else:
        return False



# LT Subdomain Project
@subdomain_bp.route("/add_subdomain", methods=["POST"])
@jwt_required()
def add_subdomain():
    if not request.is_json:
        return jsonify({"error": "Invalid input, expected JSON"}), 400

    data = request.get_json()
    subdomain_name = data.get("subdomain_name")
    redirect_link = data.get("redirect_link")
    requester_email = data.get("email")
    domain = "lt.agency"

    if not all([subdomain_name, redirect_link, requester_email]):
        print("Not all variables present")
        return jsonify({"error": "Missing required fields"}), 400

    if not is_valid_subdomain(subdomain_name):
        print("Invalid Subdomain")
        return jsonify({"error": "Invalid subdomain name provided"}), 400

    if not is_valid_url(redirect_link):
        print("Invalid URL")
        return jsonify({"error": "Invalid URL provided"}), 400
    # Construct the document to insert
    document = {
        "subdomain": subdomain_name,
        "domain": domain,
        "redirect_url": redirect_link,
        "email": requester_email,
        "is_active": False,  # Initially set to False
    }

    aws_region = "us-east-2"
    email_body = f"The user {requester_email} has successfully requested the subdomain {subdomain_name}.{domain}\n\nThis subdomain will redirect to {redirect_link}\n\nPlease fulfill this request within the next 48 hours."

    try:
        current_app.subdomain_collection.insert_one(document)
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
                    "ToAddresses": ["jarod.johnson@lt.agency"],
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
                        "Data": "LT URL Redirect Request",
                    },
                },
                Source="no-reply@laneterraleverapi.org",  # Your verified address
            )

        except ClientError as e:
            print(f"An error occurred: {e.response['Error']['Message']}")
        else:
            print(f"Email sent! Message ID: {response['MessageId']}")

        return jsonify({"message": "Subdomain added successfully"}), 200

    except DuplicateKeyError:
        return jsonify({"error": "Subdomain already in use"}), 409
    except Exception as e:
        # Handle any other exceptions
        return jsonify({"error": str(e)}), 500

@subdomain_bp.route("/get_subdomains_by_email", methods=["GET"])
@jwt_required()
def get_subdomains_by_email():
    requester_email = request.args.get("email")  # Get email parameter from the query string

    if not requester_email:
        return jsonify({"error": "Missing required email parameter"}), 400

    try:
        # Query the database for all subdomains owned by the given email
        subdomains = current_app.subdomain_collection.find({"email": requester_email})
        subdomains_list = [{
            "subdomain": sub["subdomain"],
            "domain": sub["domain"],
            "redirect_url": sub["redirect_url"],
            "is_active": sub["is_active"],
        } for sub in subdomains]

        return jsonify({"subdomains": subdomains_list}), 200

    except Exception as e:
        # Handle any other exceptions
        return jsonify({"error": "Failed to retrieve subdomains", "details": str(e)}), 500