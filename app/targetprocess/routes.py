from flask import Blueprint, current_app, request, jsonify
from functools import wraps
import os
from datetime import datetime
import pytz
import requests
from bs4 import BeautifulSoup
from pymongo.errors import PyMongoError, DuplicateKeyError
from pymongo import UpdateOne
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import logging

targetprocess_bp = Blueprint("targetprocess_bp", __name__)

TARGETPROCESS_DIR = os.path.dirname(os.path.abspath(__file__))

# Define the scope and credentials file
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(f'{TARGETPROCESS_DIR}/google/LT_google_sheet_sa.json', scope)

# Authorize the client
client = gspread.authorize(creds)

from . import routes

def api_key_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get("X-API-KEY")
        server_api_key = os.getenv("TP_VALIDATION_KEY")
        if not api_key or api_key != server_api_key:
            return jsonify({'message': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function


def clean_html(raw_html):
    soup = BeautifulSoup(raw_html, 'html.parser')
    cleaned_text = soup.get_text(separator=" ")
    return cleaned_text.replace('\n', '')

# @targetprocess_bp.route('/add_csv', methods=['POST'])
# def add_csv_to_mongo():
#     csv_file = f"{TARGETPROCESS_DIR}/tp_sow_connection.csv"
#     df = pd.read_csv(csv_file)
#     df.columns = ['project_num', 'project_name', 'SowKey']
#     data = df.to_dict(orient='records')
#     current_app.project_sow_field_collection.insert_many(data)
#     return jsonify({"Message": "success"})

# @targetprocess_bp.route('/update-all-sow-fields', methods=['POST'])
# def update_all_sow_fields():
#     try:
#         # Open the Google Sheet by its ID
#         spreadsheet_id = os.getenv("TP_GOOGLE_SHEET_ID")
#         spreadsheet = client.open_by_key(spreadsheet_id)

#         # Select the specific sheet by title
#         worksheet = spreadsheet.worksheet("Projects to Verify - May 2024")

#         # Get all the data from the sheet
#         sheet_data = worksheet.get_all_records()

#         # Convert the sheet data to a DataFrame
#         df = pd.DataFrame(sheet_data)

#         # Filter rows where SowKey is not empty and not just whitespace
#         df_filtered = df[df['SowKey'].notna() & df['SowKey'].str.strip().astype(bool)]

#         # Iterate over the filtered rows and call /set_sow_key route
#         for index, row in df_filtered.iterrows():
#             project_num = row['project_num']
#             SowKey = row['SowKey']

#             # Call the /set_sow_key route
#             response = requests.post('http://127.0.0.1:5000/targetprocess/set_sow_key', json={'project_num': project_num, 'SowKey': SowKey})

#             # Check the response from the /set_sow_key route
#             if response.status_code != 200:
#                 try:
#                     error_details = response.json()
#                 except json.JSONDecodeError:
#                     error_details = response.text
#                 print(error_details)
        
#         return jsonify({"Message": "All projects updated successfully"})
#     except Exception as e:
#         return jsonify({"Message": "An error occurred", "Details": str(e)}), 500

@targetprocess_bp.route('/sheets-update-hook', methods=['POST'])
def sheets_update_hook():
    # Parse incoming JSON data
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "Invalid data"}), 400
    
    # Extract the relevant fields
    project_num = data.get('project_num')
    project_name = data.get('project_name')
    SowKey = data.get('sowKey', '')  # Default to empty string if not provided
    
    if not project_num or not project_name:
        return jsonify({"error": "Missing required fields"}), 400
    
    # Create the filter and update documents for MongoDB
    filter_doc = {"project_num": project_num}
    update_doc = {
        "$set": {
            "SowKey": SowKey,
            "project_name": project_name
        }
    }

    # Perform the upsert operation
    result = current_app.project_sow_field_collection.update_one(filter_doc, update_doc, upsert=True)
    
    if result.upserted_id:
        message = "Document inserted."
    elif result.modified_count:
        message = "Document updated."
    else:
        message = "No changes made."
    
    access_token = os.getenv("TP_API_KEY")
    api_url = f'https://laneterralever.tpondemand.com/api/v1/projects/{project_num}?access_token={access_token}&format=json'
    
    body = {
        "CustomFields": [
            {
                "Name": "SowKey",
                "Value": SowKey if SowKey is not None else ""  # Handle empty SowKey
            }
        ]
    }
    
    response = requests.post(api_url, json=body)

    try:
        response_json = response.json()
    except json.JSONDecodeError:
        logging.error(f"Invalid response from TargetProcess API for project {project_num}: {response.text}")
        return jsonify({"Message": "Invalid response from TargetProcess API", "Details": response.text}), response.status_code

    if response.status_code == 200:
        return jsonify({"Message": message})
    else:
        return jsonify({"Message": message, "Details": response_json}), response.status_code



@targetprocess_bp.route('/update-csv', methods=['GET'])
def update_csv():
    # Open the Google Sheet by its ID
    spreadsheet_id = os.getenv("TP_GOOGLE_SHEET_ID")
    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.worksheet("Projects to Verify - May 2024")
    sheet_data = worksheet.get_all_records()

    df = pd.DataFrame(sheet_data)

    access_token = os.getenv("TP_API_KEY")
    api_url = f'https://laneterralever.tpondemand.com/svc/tp-apiv2-streaming-service/stream/projects?access_token={access_token}&select={{Name,programName:Program.Name,SowKey,EarnedValuePotential}}&format=json'
    response = requests.get(api_url)
    data = response.json()

    projects = data['items']
    api_projects = pd.DataFrame(projects)

    # Rename columns to match the Google Sheet data
    api_projects = api_projects.rename(columns={'name': 'project_name', '__id': 'project_num'})

    # Keep only necessary columns
    api_projects = api_projects[['project_num', 'project_name']]
    api_projects['SowKey'] = ''

    # Identify new projects by checking which project numbers from the API are not in the Google Sheet
    new_projects = api_projects[~api_projects['project_num'].isin(df['project_num'])]

    # Append new projects to the original DataFrame
    updated_df = pd.concat([df, new_projects], ignore_index=True)

    # Sort the updated DataFrame by project_name in a case-insensitive manner
    updated_df = updated_df.sort_values(by='project_name', key=lambda col: col.str.lower())

    # Clear the existing data in the worksheet
    worksheet.clear()
    worksheet.update([updated_df.columns.values.tolist()] + updated_df.values.tolist())

    print("Google Sheet updated successfully.")

    return jsonify({"message": "success"})

@targetprocess_bp.route('/set_sow_key', methods=['POST'])
def set_tp_sow_key():
    # Get the JSON data from the request
    data = request.get_json()
    
    # Extract project_num and SowKey from the request data
    project_num = data.get('project_num')
    SowKey = data.get('SowKey')
    
    if not project_num or not SowKey:
        return jsonify({"Message": "project_num and SowKey are required"}), 400
    
    access_token = os.getenv("TP_API_KEY")
    # Define the TargetProcess API URL
    api_url = f'https://laneterralever.tpondemand.com/api/v1/projects/{project_num}?access_token={access_token}&format=json'
    
    # Define the body of the POST request
    body = {
        "CustomFields": [
            {
                "Name": "SowKey",
                "Value": SowKey
            }
        ]
    }
    
    response = requests.post(api_url, json=body)

    try:
        response_json = response.json()
    except json.JSONDecodeError:
        logging.error(f"Invalid response from TargetProcess API for project {project_num}: {response.text}")
        return jsonify({"Message": "Invalid response from TargetProcess API", "Details": response.text}), response.status_code

    if response.status_code == 200:
        return jsonify({"Message": "success"})
    else:
        return jsonify({"Message": "failed", "Details": response_json}), response.status_code

@targetprocess_bp.route('/userstories/<project_id>', methods=['GET'])
@api_key_required
def get_user_stories_by_proj_id(project_id):
    url = "https://laneterralever.tpondemand.com/svc/tp-apiv2-streaming-service/stream/userstories"
    query_params = {
        'where': f'(Project.id={project_id})',
        'select': '{Project.name,name,Description,StartDate,Effort,EarnedValueDollars,PricingTypeOverride}',
        'access_token': os.getenv('TP_API_KEY')
    }

    # Fetch data from TargetProcess API
    response = requests.get(url, params=query_params)
    if response.status_code != 200:
        return jsonify({'error': 'Failed to fetch data from TargetProcess API'}), response.status_code

    targetprocess_data = response.json().get('items', [])

    # Convert startDate from Unix timestamp to human-readable date in MST (-07:00)
    for story in targetprocess_data:
        # Clean and format the description field
        if 'description' in story:
            story['description'] = clean_html(story['description'])

        timestamp_str = story.get('startDate')
        if timestamp_str:
            try:
                timestamp_ms = int(timestamp_str[6:-7])  # Extract timestamp in milliseconds
                timestamp_dt = datetime.utcfromtimestamp(timestamp_ms / 1000).replace(tzinfo=pytz.utc)  # Convert to datetime
                local_dt = timestamp_dt.astimezone(pytz.timezone('MST7MDT'))  # Convert to MST time zone
                story['startDate'] = local_dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, IndexError):
                story['startDate'] = 'Invalid Date'  # Handle any potential conversion errors
        else:
            story['startDate'] = 'N/A'  # Or any other default value you prefer

        # Fetch tasks related to the current story
        story_id = story.get('__id')
        if story_id:
            tasks_url = f"https://laneterralever.tpondemand.com/api/v1/Tasks"
            tasks_query_params = {
                'where': f'(UserStory.id eq {story_id})',
                'access_token': os.getenv('TP_API_KEY'),
                'take': 10,
                'format': 'json',
                'include': '[Name,Effort]'
            }
            tasks_response = requests.get(tasks_url, params=tasks_query_params)
            if tasks_response.status_code == 200:
                tasks_data = tasks_response.json().get('Items', [])
                # Remove Id and ResourceType fields from tasks
                for task in tasks_data:
                    task.pop('Id', None)
                    task.pop('ResourceType', None)
                story['tasks'] = tasks_data
            else:
                story['tasks'] = []  # Or handle errors as needed

    return jsonify(targetprocess_data)

@targetprocess_bp.route('/userstories/sow/<file_name>', methods=['GET'])
@api_key_required
def get_user_stories(file_name):
    url = "https://laneterralever.tpondemand.com/svc/tp-apiv2-streaming-service/stream/userstories"
    query_params = {
        'where': f'(Project.SowKey={file_name})',
        'select': '{name,Description,StartDate,Effort,EarnedValueDollars,PricingTypeOverride}',
        'access_token': os.getenv('TP_API_KEY')
    }

    # Fetch data from TargetProcess API
    response = requests.get(url, params=query_params)
    if response.status_code != 200:
        return jsonify({'error': 'Failed to fetch data from TargetProcess API'}), response.status_code

    targetprocess_data = response.json().get('items', [])

    # Convert startDate from Unix timestamp to human-readable date in MST (-07:00)
    for story in targetprocess_data:
        # Clean and format the description field
        if 'description' in story:
            story['description'] = clean_html(story['description'])

        timestamp_str = story.get('startDate')
        if timestamp_str:
            try:
                timestamp_ms = int(timestamp_str[6:-7])  # Extract timestamp in milliseconds
                timestamp_dt = datetime.utcfromtimestamp(timestamp_ms / 1000).replace(tzinfo=pytz.utc)  # Convert to datetime
                local_dt = timestamp_dt.astimezone(pytz.timezone('MST7MDT'))  # Convert to MST time zone
                story['startDate'] = local_dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, IndexError):
                story['startDate'] = 'Invalid Date'  # Handle any potential conversion errors
        else:
            story['startDate'] = 'N/A'  # Or any other default value you prefer

        # Fetch tasks related to the current story
        story_id = story.get('__id')
        if story_id:
            tasks_url = f"https://laneterralever.tpondemand.com/api/v1/Tasks"
            tasks_query_params = {
                'where': f'(UserStory.id eq {story_id})',
                'access_token': os.getenv('TP_API_KEY'),
                'take': 10,
                'format': 'json',
                'include': '[Name,Effort]'
            }
            tasks_response = requests.get(tasks_url, params=tasks_query_params)
            if tasks_response.status_code == 200:
                tasks_data = tasks_response.json().get('Items', [])
                # Remove Id and ResourceType fields from tasks
                for task in tasks_data:
                    task.pop('Id', None)
                    task.pop('ResourceType', None)
                story['tasks'] = tasks_data
            else:
                story['tasks'] = []  # Or handle errors as needed

    return jsonify(targetprocess_data)