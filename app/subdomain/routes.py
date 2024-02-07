from flask import Blueprint, current_app, request, jsonify
from flask_jwt_extended import jwt_required
import os


subdomain_bp = Blueprint("subdomain_bp", __name__)

from . import routes

INSTALL_ID = os.getenv("wp_engine_install_id")


@subdomain_bp.route("/delete_subdomain", methods=["DELETE"])
@jwt_required()
def delete_subdomain():
    # Extract subdomain and domain from request
    subdomain = request.json.get("subdomain")
    domain = request.json.get("domain")

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

        # If delete count is greater than 0, it means the operation was successful
        return jsonify({"message": "Subdomain deleted successfully"}), 200

    except PyMongoError as e:
        # Handle any other database exceptions
        return jsonify({"error": "Failed to delete subdomain", "details": str(e)}), 500


# LT Subdomain Project
@subdomain_bp.route("/add_subdomain", methods=["POST"])
@jwt_required()
def add_subdomain():
    # Get subdomain information from the request
    subdomain_name = request.json.get("subdomain_name")
    domain_name = request.json.get("domain_name")
    redirect_link = request.json.get("redirect_link")
    requester_email = request.json.get("email")

    domain = ""
    if domain_name:
        domain = "lt.agency"
    else:
        domain = "laneterraleverapi.org"

    if not all([subdomain_name, domain, redirect_link, requester_email]):
        return jsonify({"error": "Missing required fields"}), 400

    # Construct the document to insert
    document = {
        "subdomain": subdomain_name,
        "domain": domain,
        "redirect_url": redirect_link,
        "email": requester_email,
        "is_active": False,  # Initially set to False
        "ssl_active": False,
    }

    try:
        current_app.subdomain_collection.insert_one(document)
        return jsonify({"message": "Subdomain added successfully"}), 201

    except DuplicateKeyError:
        return jsonify({"error": "Subdomain already in use"}), 409
    except Exception as e:
        # Handle any other exceptions
        return jsonify({"error": str(e)}), 500
