from flask import Blueprint, current_app, request, jsonify
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google.auth import credentials as google_auth_credentials
from google.ads.googleads.client import GoogleAdsClient
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from datetime import datetime
from calendar import monthrange
import yaml
import os

basecamp_bp = Blueprint("basecamp_bp", __name__)

from . import routes


# Basecamp Media Pacing Project
# Define the function to calculate percentage of the month passed outside of Flask route
def percentage_of_month_passed():
    now = datetime.now()
    start_of_month = datetime(now.year, now.month, 1)
    total_days_in_month = monthrange(now.year, now.month)[1]
    end_of_month = datetime(now.year, now.month, total_days_in_month)
    percentage_passed = (now - start_of_month).total_seconds() / (
        end_of_month - start_of_month
    ).total_seconds()
    return percentage_passed


# Basecamp Media Pacing Project
def update_date(credentials):
    RANGE_NAME = "'% of Month'!D2"  # The cell range to update

    # Build a Sheets API service client
    sheets_service = build("sheets", "v4", credentials=credentials)

    # Calculate the percentage of the month passed
    percentage_passed = percentage_of_month_passed()

    # Specify the value to update in the sheet
    values = [[f"{percentage_passed:.5f}"]]
    body = {"values": values}

    # Call the Sheets API to update the cell
    result = (
        sheets_service.spreadsheets()
        .values()
        .update(
            spreadsheetId=os.getenv("SHEET_ID"),
            range=RANGE_NAME,
            valueInputOption="USER_ENTERED",
            body=body,
        )
        .execute()
    )
    return


# Basecamp Media Pacing Project
def create_google_ads_yaml(
    client_id, client_secret, refresh_token, developer_token, yaml_file_path
):
    # Define the config structure
    config = {
        "developer_token": developer_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "login_customer_id": "3485321694",  # Optional, set this to your manager account ID if you have one
        "use_proto_plus": True,
    }

    # Write the config to a yaml file
    with open(yaml_file_path, "w") as file:
        yaml.dump(config, file, default_flow_style=False)


# Basecamp Media Pacing Project
def delete_google_ads_yaml(yaml_file_path):
    # Delete the yaml file
    if os.path.exists(yaml_file_path):
        os.remove(yaml_file_path)


# Basecamp Media Pacing Project
# Function to initialize the Google Ads client with the given refresh token
def create_google_ads_client(refresh_token, client_id, client_secret, developer_token):
    # Path to the temporary .yaml config file
    yaml_file_path = "./google/google-ads.yaml"

    # Create the temporary .yaml config file with the provided credentials
    create_google_ads_yaml(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        developer_token=developer_token,
        yaml_file_path=yaml_file_path,
    )

    # Initialize the Google Ads client with the credentials
    google_ads_client = GoogleAdsClient.load_from_storage(yaml_file_path)
    return google_ads_client


# Basecamp Media Pacing Project
# Function to make an API call using the Google Ads client
def make_api_call(google_ads_client, customer_id):
    # Example of how to construct a service client using the Google Ads client
    service = google_ads_client.get_service("GoogleAdsService")

    # Modified query to get campaign name and cost
    query = """
        SELECT campaign.name, metrics.cost_micros
        FROM campaign
        WHERE campaign.status = 'ENABLED'
    """

    # Make the API call
    response = service.search_stream(customer_id=customer_id, query=query)

    # Handle the API response
    for batch in response:
        for row in batch.results:
            campaign_name = row.campaign.name.value
            campaign_cost = (
                int(row.metrics.cost_micros.value) / 1e6
                if row.metrics.cost_micros
                else 0
            )  # Convert micros to currency unit
            print(f"Campaign Name: {campaign_name}, Cost: {campaign_cost}")

    return response


# Basecamp Media Pacing Project
# Function to copy a Google Sheet file to another folder
def copy_sheet_to_another_folder(sheet_id, destination_folder_id):
    now = datetime.now()
    formatted_date_time = now.strftime("%Y-%m-%d %H:%M:%S")
    # Specify the title of the copy if provided
    file_metadata = {"parents": [destination_folder_id]}

    file_metadata["name"] = f"BaseCamp Media Pacing Backup - {formatted_date_time}"

    # Make the API call to copy the file
    copied_file = (
        service.files()
        .copy(fileId=sheet_id, body=file_metadata, supportsAllDrives=True)
        .execute()
    )

    # Return the ID of the new copy
    return copied_file["id"]


# Basecamp Media Pacing Project
# Function to update campaign costs in the Google Sheet
def update_campaign_costs_in_sheet(
    spreadsheet_id, sheet_name, campaign_data, credentials
):
    # Build the Sheets API service client using the same credentials
    sheets_service = build("sheets", "v4", credentials=credentials)

    # Read campaign names from the Google Sheet
    range_name = f"{sheet_name}!A:A"  # Change to your actual sheet name
    response = (
        sheets_service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    sheet_campaign_names = response.get("values", [])

    # Prepare the update request body
    update_values = []

    # Associate API campaign names with names in the sheet and prepare updates
    for row_number, sheet_campaign in enumerate(
        sheet_campaign_names, start=1
    ):  # Sheets are 1-indexed
        sheet_campaign_name = sheet_campaign[
            0
        ]  # Assuming campaign name is the first column
        if sheet_campaign_name in campaign_data:
            update_values.append(
                {
                    "range": f"{sheet_name}!E{row_number}",
                    "values": [[campaign_data[sheet_campaign_name]]],
                }
            )

    # Batch update the sheet if there are updates to be made
    if update_values:
        body = {"valueInputOption": "USER_ENTERED", "data": update_values}
        sheets_service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id, body=body
        ).execute()


# Basecamp Media Pacing Project
@basecamp_bp.route("/update_sheet", methods=["POST"])
def google_authenticate():
    franchise = request.json.get("franchise")
    customer_id = ""
    if franchise == "UC":
        customer_id = "8901228398"
    else:
        customer_id = "9224140672"

    # Set up your Google Drive API client
    credentials = Credentials.from_service_account_file(
        "./google/lt-basecamp-service-account.json",
        scopes=[
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.metadata",
            "https://www.googleapis.com/auth/spreadsheets",
        ],
    )
    service = build("drive", "v3", credentials=credentials)

    SCOPES = ["https://www.googleapis.com/auth/adwords"]
    CLIENT_SECRETS_FILE = "./google/client-secret.json"
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    cred = flow.run_local_server()
    copy_sheet_to_another_folder(
        os.getenv("FILE_ID"), os.getenv("DESTINATION_FOLDER_ID")
    )
    refresh_token = cred.refresh_token
    google_ads_client = create_google_ads_client(
        refresh_token,
        os.getenv("client_id"),
        os.getenv("client_secret"),
        os.getenv("developer_token"),
    )
    data = make_api_call(google_ads_client, customer_id)
    update_campaign_costs_in_sheet(
        os.getenv("FILE_ID"), "Google Ads", data, credentials
    )
    update_date(credentials)
    delete_google_ads_yaml("./google/google-ads.yaml")
    return jsonify({"msg": "success"}), 200
