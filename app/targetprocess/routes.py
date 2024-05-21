from flask import Blueprint, current_app, request, jsonify
from functools import wraps
import os
from datetime import datetime
import pytz
import requests
from bs4 import BeautifulSoup


targetprocess_bp = Blueprint("targetprocess_bp", __name__)

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
    
@targetprocess_bp.route('/userstories/<project_id>', methods=['GET'])
@api_key_required
def get_user_stories(project_id):
    url = "https://laneterralever.tpondemand.com/svc/tp-apiv2-streaming-service/stream/userstories"
    query_params = {
        'where': f'(Project.id={project_id})',
        'select': '{Name,Description,StartDate,Effort,EarnedValueDollars,PricingTypeOverride}',
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