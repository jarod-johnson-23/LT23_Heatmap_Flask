from flask import Blueprint, current_app, request, jsonify
from functools import wraps
import os
import io
import re
from datetime import datetime, timezone, timedelta
import pytz
import requests
from bs4 import BeautifulSoup
from pymongo.errors import PyMongoError, DuplicateKeyError
from pymongo import UpdateOne
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import json
import logging
from flask_jwt_extended import jwt_required
from openai import OpenAI
import pdfplumber
from PIL import Image
from pdf2image import convert_from_path
from docx import Document
import pytesseract
import html


targetprocess_bp = Blueprint("targetprocess_bp", __name__)

# AWS SES Configuration
AWS_REGION = "us-east-2"
AWS_ACCESS_KEY = os.getenv("aws_access_key_id")
AWS_SECRET_KEY = os.getenv("aws_secret_access_key")
EMAIL_SOURCE = "no-reply@laneterraleverapi.org"  # Must be a verified SES sender email
EMAIL_RECIPIENT = "devteam@laneterralever.com"
EMAIL_SUBJECT = "Recent DEV-C TP Comments (Last 24 Hours)"

TARGETPROCESS_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "https://laneterralever.tpondemand.com/api/v2"
ACCESS_TOKEN = os.getenv("TP_API_KEY")

# Define the scope and credentials file
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/drive.readonly"]
creds = ServiceAccountCredentials.from_json_keyfile_name(f'{TARGETPROCESS_DIR}/google/LT_google_sheet_sa.json', scope)

# Authorize the client
client = gspread.authorize(creds)

client = OpenAI(
    organization=os.getenv("openai_organization"),
    api_key=os.getenv("openai_api_key"),
)

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

def fetch_stories_with_recent_comments():
    api_url = (
        f"{BASE_URL}/UserStory?"
        "where=comments.where(owner.id==384 and createDate>Today.AddDays(-1)).count()>0"
        "&select={id,name,comments:comments.where(owner.id==384 and createDate>Today.AddDays(-1)).select({id,createDate,description})}"
        f"&access_token={ACCESS_TOKEN}"
    )

    response = requests.get(api_url, headers={"Content-Type": "application/json"})
    if response.status_code == 200:
        data = response.json()
        return data.get("items", [])  # Extract the relevant stories
    else:
        print(f"Error fetching data: {response.status_code} - {response.text}")
        return []

# ------------------------------------------------------------------------------
# Utility functions for cleaning comment text and parsing dates
# ------------------------------------------------------------------------------
def clean_comment_text(text):
    return text.strip().replace("\n", " ").replace("\r", "")

def parse_tp_date(tp_date):
    """
    Converts a Targetprocess date string of the form /Date(1738252718000-0600)/
    into an ISO 8601 string with a Z suffix. (You may adjust this as needed.)
    """
    match = re.search(r"/Date\((\d+)([+-]\d{4})\)/", tp_date)
    if match:
        timestamp_ms = int(match.group(1))
        utc_offset = match.group(2)
        timestamp_s = timestamp_ms / 1000.0
        dt = datetime.fromtimestamp(timestamp_s, tz=timezone.utc)
        if utc_offset:
            # Adjust by the timezone offset (if desired)
            offset_hours = int(utc_offset[:3])
            dt += timedelta(hours=offset_hours)
        # Return a non-ambiguous ISO 8601 formatted string (with milliseconds and Z)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return tp_date  # Return as-is if parsing fails



def clean_comment_text(text, remove_tags=True):
    # First, decode HTML entities (e.g., &#64; becomes @)
    text = html.unescape(text)
    
    if remove_tags:
        # Remove HTML tags using BeautifulSoup
        soup = BeautifulSoup(text, "html.parser")
        text = soup.get_text()
    
    # Clean up whitespace and newlines
    return text.strip().replace("\n", " ").replace("\r", "")

# ------------------------------------------------------------------------------
# New function to insert comment data into the Airtable Comments table
# ------------------------------------------------------------------------------
def insert_comments_into_airtable():
    stories = fetch_stories_with_recent_comments()
    if not stories:
        print("No recent comments to insert into Airtable")
        return True  # Nothing to insert is not necessarily an error

    records = []
    for story in stories:
        story_id = story.get("id")
        story_name = story.get("name")
        # Ensure story_id is a string if your Airtable field expects text.
        story_id_str = str(story_id) if story_id is not None else ""
        
        # Each comment in the story will be inserted as a separate record
        for comment in story.get("comments", []):
            comment_time = parse_tp_date(comment.get("createDate"))
            # Clean the comment content:
            comment_text = clean_comment_text(comment.get("description"), remove_tags=True)
            record = {
                "fields": {
                    "Story ID": story_id_str,
                    "Story Name": story_name,
                    "Comment time": comment_time,
                    "Comment": comment_text
                }
            }
            records.append(record)

    # Airtable API endpoint for your Comments table
    airtable_api_url = "https://api.airtable.com/v0/appVKUAQ6alU3EWrK/Comments"
    headers = {
        "Authorization": f"Bearer {os.getenv('AIRTABLE_API_KEY')}",
        "Content-Type": "application/json"
    }

    # Airtable API supports creating up to 10 records per request.
    chunk_size = 10
    success = True
    for i in range(0, len(records), chunk_size):
        chunk = records[i:i+chunk_size]
        data = {"records": chunk}
        response = requests.post(airtable_api_url, headers=headers, json=data, params={"typecast": "true"})
        if response.status_code == 200:
            print("Records inserted successfully:", response.json())
        else:
            print(f"Error inserting records into Airtable: {response.status_code} - {response.text}")
            success = False

    return success

# ------------------------------------------------------------------------------
# Flask route to call the Airtable insertion instead of sending an email
# ------------------------------------------------------------------------------
@targetprocess_bp.route('/insert-comments', methods=['GET'])
def insert_comments():
    if insert_comments_into_airtable():
        return jsonify({"message": "Comments inserted successfully"}), 200
    else:
        return jsonify({"error": "Failed to insert some comments"}), 500


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

@targetprocess_bp.route('/upload-sow-from-gdrive', methods=['POST'])
def upload_sow_from_gdrive():
    try:
        data = request.get_json()
        google_doc_url = data.get('google_doc_url')
        
        if not google_doc_url:
            return jsonify({"error": "Missing google_doc_url"}), 400
        
        # Extract file ID from the Google Doc URL
        file_id = google_doc_url.split('/')[5]
        
        # Build the Drive API service
        service = build('drive', 'v3', credentials=creds)
        
        # Get the file metadata to retrieve the original filename
        file_metadata = service.files().get(fileId=file_id, fields='name').execute()
        original_filename = file_metadata.get('name')
        
        # Download the Google Doc as PDF
        request = service.files().export_media(fileId=file_id, mimeType='application/pdf')
        download_dir = f'{TARGETPROCESS_DIR}/temp_files'  # Specify your desired directory
        os.makedirs(download_dir, exist_ok=True)
        file_name = os.path.join(download_dir, f'{original_filename}.pdf')
        fh = io.FileIO(file_name, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            print(f"Download {int(status.progress() * 100)}%.")

        print(f"File downloaded as {file_name}")
        
        # Perform operations on the downloaded file
        with open(file_name, 'rb') as file:
            content = file.read()
            # Do something with the content, for example, return its length
            file_content_length = len(content)
        
        return jsonify({"message": "File downloaded and processed successfully", "original_filename": original_filename, "file_content_length": file_content_length}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    # List of common file extensions to remove
    common_extensions = ['.pdf', '.docx', '.doc', '.txt', '.xlsx']

    # Remove common file extensions from file_name if present
    for ext in common_extensions:
        if file_name.endswith(ext):
            file_name = file_name[:-len(ext)]
            break

    url = "https://laneterralever.tpondemand.com/svc/tp-apiv2-streaming-service/stream/userstories"
    query_params = {
        'where': f'(Project.SowKey=\'{file_name}\')',
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

def convert_pdf_to_txt(input_stream):
    """Convert a PDF file to a text file stream. Use OCR if the PDF is scanned."""
    all_text = ''
    with pdfplumber.open(input_stream) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                all_text += page_text
    if not all_text:
        # Use OCR for scanned PDF
        all_text = extract_text_via_ocr(input_stream)
    return io.StringIO(all_text)

def extract_text_via_ocr(pdf_stream):
    """Extract text from a scanned PDF using OCR."""
    images = convert_from_path(pdf_stream)
    extracted_text = ''
    for img in images:
        extracted_text += pytesseract.image_to_string(img)
    return extracted_text

def convert_docx_to_txt(input_stream):
    """Convert a DOCX file to a text file stream."""
    doc = Document(input_stream)
    all_text = []
    for paragraph in doc.paragraphs:
        all_text.append(paragraph.text)
    return io.StringIO('\n'.join(all_text))

def process_file(file_stream, ext):
    """Process a file and upload it to the vector store."""
    if ext == "txt":
        file_stream.seek(0)
    elif ext == "pdf":
        file_stream = convert_pdf_to_txt(file_stream)
    elif ext == "docx":
        file_stream = convert_docx_to_txt(file_stream)
    else:
        return jsonify({"error": "Unsupported file type"}), 400
    
    client.beta.vector_stores.file_batches.upload_and_poll(
        vector_store_id="vs_ziPsnk1FAuN4iH55CnZePmLs", files=[file_stream]
    )

def download_file_from_google_drive(file_id, creds):
    """Download a file from Google Drive."""
    service = build('drive', 'v3', credentials=creds)
    request = service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    file_stream.seek(0)
    return file_stream

def download_file_from_google_docs(file_id, creds):
    """Download a Google Doc file as a PDF."""
    service = build('drive', 'v3', credentials=creds)
    request = service.files().export_media(fileId=file_id, mimeType='application/pdf')
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    file_stream.seek(0)
    return file_stream

@targetprocess_bp.route('/sow/file-upload', methods=['POST'])
@jwt_required()
def sow_file_upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    file_stream = io.BytesIO(file.read())
    ext = file.filename.split('.')[-1].lower()
    process_file(file_stream, ext)
    return jsonify({"message": "File uploaded and processed successfully"}), 200

@targetprocess_bp.route('/sow/google-doc-upload', methods=['POST'])
@jwt_required()
def sow_gdoc_upload():
    data = request.get_json()
    if 'doc_url' not in data:
        return jsonify({"error": "No Google Doc URL provided"}), 400
    doc_url = data['doc_url']
    file_id = doc_url.split('/')[-2]

    creds = service_account.Credentials.from_service_account_file(
        'path_to_service_account.json', scopes=[
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file"
        ]
    )
    file_stream = download_file_from_google_docs(file_id, creds)
    process_file(file_stream, 'pdf')  # Google Docs are exported as PDF
    return jsonify({"message": "Google Doc downloaded and processed successfully"}), 200

@targetprocess_bp.route('/sow/google-drive-upload', methods=['POST'])
@jwt_required()
def sow_gdrive_upload():
    data = request.get_json()
    if 'drive_url' not in data:
        return jsonify({"error": "No Google Drive URL provided"}), 400
    drive_url = data['drive_url']
    file_id = drive_url.split('/')[-2]

    creds = service_account.Credentials.from_service_account_file(
        'path_to_service_account.json', scopes=[
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file"
        ]
    )
    file_stream = download_file_from_google_drive(file_id, creds)
    file_ext = data.get('file_extension', 'pdf')  # Assume PDF by default, adjust as needed
    process_file(file_stream, file_ext)
    return jsonify({"message": "Google Drive file downloaded and processed successfully"}), 200