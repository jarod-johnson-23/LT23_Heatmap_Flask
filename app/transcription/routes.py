from flask import Blueprint, request, jsonify, send_file, current_app, send_from_directory
from werkzeug.utils import secure_filename
import os
import requests
import re
from datetime import datetime
from collections import defaultdict
from openai import OpenAI
from pydub import AudioSegment

transcript_bp = Blueprint("transcript_bp", __name__)


from . import routes

TRANSCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")

client = OpenAI(
    organization=os.getenv("openai_organization"),
    api_key=os.getenv("openai_api_key"),
)

def speaker_diarization(file_path):
  api_key = os.getenv('deepgram_api_key')

  # Construct the API endpoint
  url = 'https://api.deepgram.com/v1/listen'
  params = {
      'diarize': 'true',
      'punctuate': 'true',
      'utterances': 'true'
  }
  headers = {
      'Authorization': f'Token {api_key}',
      'Content-Type': 'audio/mp3'
  }

  # Open the file in binary read mode
  with open(file_path, 'rb') as file:
      # Make the API request
      response = requests.post(url, headers=headers, params=params, data=file)
      
  # Check for successful response
  if response.status_code == 200:
      # Parse the JSON response
      response_json = response.json()
      print(response.json())
      # Extract utterances
      utterances = response_json.get('results', {}).get('utterances', [])
      
      # List to hold speaker IDs and timestamps
      speaker_details = []
      
      # Collect speaker ID and timestamps
      for utterance in utterances:
          speaker_id = utterance.get('speaker')
          timestamp = utterance.get('start')
          end_timestamp = utterance.get('end')
          
          speaker_detail = {
              'speaker_id': speaker_id,
              'start_timestamp': timestamp,
              'end_timestamp': end_timestamp
          }
          
          speaker_details.append(speaker_detail)
      # Now speaker_details contains the requested information
      return speaker_details
  else:
      print(f"Error: API request failed with status code {response.status_code}")

def perform_asr(file_path, prompt=""):
    prompt += " LT, LaneTerralever"
    audio_file = open(file_path, "rb")
    response = client.audio.transcriptions.create(
        file=audio_file,
        model="whisper-1",
        response_format="verbose_json",
        timestamp_granularities=["segment"],
        language="en",
        prompt=prompt
    )

    print(response)
    speaker_segments = response.segments

    # Initialize a list to hold transcription details
    transcription_details = []

    # The result contains a 'segments' key with the transcription and timestamps for each segment
    for segment in speaker_segments:
        start_time = segment['start']
        end_time = segment['end']
        text = segment['text']

        # Append transcription details to the list
        transcription_details.append({
            'start_time': start_time,
            'end_time': end_time,
            'text': text
        })

    return transcription_details


def combine_speaker_and_transcription(speaker_results, transcription_details):
    combined_transcript = []
    speaker_lines = {}  # To hold the lines spoken by each speaker
    
    def calculate_overlap(speaker, trans_start, trans_end):
        overlap_start = max(speaker['start_timestamp'], trans_start)
        overlap_end = min(speaker['end_timestamp'], trans_end)        
        overlap_duration = max(0, overlap_end - overlap_start)
        return overlap_duration

    for transcription in transcription_details:
        trans_start = transcription['start_time']
        trans_end = transcription['end_time']

        # Generate list of (speaker, overlap_time) tuples
        overlap_list = [
            (speaker['speaker_id'], calculate_overlap(speaker, trans_start, trans_end)) 
            for speaker in speaker_results
        ]

        # Find the maximum overlap time
        max_overlap = max([time for _, time in overlap_list], default=0)

        # Determine the speakers with considerable overlaps > 50% of max overlap time
        chosen_speakers = {
            speaker_id for speaker_id, overlap_time in overlap_list 
            if overlap_time >= 0.5 * max_overlap and overlap_time > 0
        }

        # Create the output structure if speakers have been identified
        if chosen_speakers:
            combined_segment = {
                'speaker_ids': list(chosen_speakers),
                'start_time': trans_start,
                'end_time': trans_end, 
                'text': transcription['text']
            }
            combined_transcript.append(combined_segment)
            
            # Record the lines for speaker summaries
            for speaker_id in chosen_speakers:
                if speaker_id not in speaker_lines:
                    speaker_lines[speaker_id] = []
                speaker_lines[speaker_id].append(transcription['text'])
        else:
            print(f"No speaker found for the segment {trans_start}s - {trans_end}s.")
    
    # Generate speaker summaries with the first few lines
    speaker_summaries = {f"Speaker {speaker_id}": " ".join(lines[:3]) for speaker_id, lines in speaker_lines.items()}

    return combined_transcript, speaker_summaries

def display_transcript(transcript_data):
    # Generate a filename with the current timestamp
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"final_transcript_{timestamp_str}.txt"
    
    with open(f'{current_app.root_path}/transcription/files/transcripts/{filename}', 'w') as file:
        # Initialize previous speaker and start time for combining segments
        previous_speaker_ids = None
        segment_start_time = None
        segment_texts = []

        def write_segment(file, speaker_ids, start_time, end_time, texts):
            speaker_label = " & ".join(f"Speaker {speaker_id}" for speaker_id in speaker_ids)
                
            def format_time(seconds):
                minutes = int(seconds) // 60
                seconds = round(seconds % 60)
                return f"{minutes}:{seconds:02d}"  # format seconds as two digits
            
            formatted_start = format_time(float(start_time))
            formatted_end = format_time(float(end_time))
            
            # Write combined speaker/timestamp line
            file.write(f"[{speaker_label}] ({formatted_start} - {formatted_end}):\n")
            # Write each text segment separated by newlines
            for text in texts:
                file.write(text + "\n")
            file.write("\n")

        for segment in transcript_data:

            # If the segment has the same speaker(s), combine the texts
            if segment['speaker_ids'] == previous_speaker_ids:
                segment_texts.append(segment['text'])
                end_time = segment['end_time']  # Update the end time to the current segment's end time
            else:
                # If we have a previous speaker, write the combined segment
                if previous_speaker_ids is not None:
                    write_segment(file, previous_speaker_ids, segment_start_time, end_time, segment_texts)
                
                # Reset for the new speaker segment
                previous_speaker_ids = segment['speaker_ids']
                segment_start_time = segment['start_time']
                end_time = segment['end_time']
                segment_texts = [segment['text']]

        # Make sure to write the last segment
        if previous_speaker_ids is not None:
            write_segment(file, previous_speaker_ids, segment_start_time, end_time, segment_texts)

    return filename

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'mp3', 'wav', 'mp4', 'm4a', 'aac', 'ogg'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def convert_to_mp3(audio_file_path, filename):
    try:
        audio = AudioSegment.from_file(audio_file_path)
        mp3_filename = f"{os.path.splitext(filename)[0]}.mp3"
        mp3_file_path = os.path.join(os.path.dirname(audio_file_path), mp3_filename)
        audio.export(mp3_file_path, format="mp3")
        return mp3_file_path
    except Exception as e:
        print(f"Error converting {filename} to MP3: {e}")
        return None

@transcript_bp.route("/mp3", methods=["POST"])
def init_transcription():
    if 'audio_input' not in request.files:
        return jsonify({"error": "No file part"}), 400

    audio_file = request.files['audio_input']
    prompt = request.form.get('prompt')

    if audio_file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if audio_file and allowed_file(audio_file.filename):
        filename = secure_filename(audio_file.filename)
        
        # Create a unique or specific directory for audio files if doesn't exist
        audio_file_path = os.path.join(current_app.root_path, 'transcription/files/audio', filename)
        os.makedirs(os.path.dirname(audio_file_path), exist_ok=True)

        # Save the uploaded file
        audio_file.save(audio_file_path)

        # Convert to MP3 if necessary
        if not filename.endswith('.mp3'):
            audio_file_path = convert_to_mp3(audio_file_path, filename)
            if audio_file_path is None:
                return jsonify({"error": "Failed to convert file to MP3"}), 400

        speaker_results = speaker_diarization(audio_file_path)
        transcription_details = perform_asr(audio_file_path, prompt)
        full_transcript, speaker_summaries = combine_speaker_and_transcription(speaker_results, transcription_details)
        transcript_file = display_transcript(full_transcript)

        try:
            os.remove(audio_file_path)
        except OSError as e:
            print(f"Error: {audio_file_path} : {e.strerror}")

        return jsonify({"message": transcript_file, "summaries": speaker_summaries}), 200
    else:
        return jsonify({"error": "File type not allowed"}), 400

def process_transcript_file(file_path):
    # Create a dictionary to store the dialogues
    speaker_dialogues = defaultdict(str)
    
    # Open the transcript file
    with open(file_path, 'r') as file:
        content = file.read()
    
    # Regular expression to match speaker patterns and capture multiline dialogue
    pattern = re.compile(r'\[Speaker (\d+)(?: & Speaker \d+)?\] \(\d+\.\d+s - \d+\.\d+s\):\n(.*?)(?=\n\[|\n{2,}|\Z)', re.DOTALL)
    
    matches = pattern.findall(content)
    
    for match in matches:
        # Handling multiple speakers
        if '&' in match[0]:
            continue
        
        # "Speaker {num}" format for keys
        speaker_id = f"Speaker {match[0]}"
        dialogue = match[1]
        clean_dialogue = ' '.join(dialogue.splitlines()).strip()
        
        # Append the cleaned dialogue
        speaker_dialogues[speaker_id] += clean_dialogue + ' '
    
    # Truncate the dialogue to 200 characters at the nearest word and add "..."
    for speaker, text in speaker_dialogues.items():
        if len(text) > 200:
            truncated_text = text[:200].rsplit(' ', 1)[0] + '...'
            speaker_dialogues[speaker] = truncated_text
        else:
            speaker_dialogues[speaker] = text.strip()
    
    # Convert defaultdict to a regular dictionary for JSON output
    return dict(speaker_dialogues)

@transcript_bp.route("/add_speakers_and_send", methods=["POST"])
def finalize_transcript():
    data = request.json
    filename = data["filename"]
    speaker_names = data["speaker_names"]  # Expecting {'Speaker 0': 'Alice', 'Speaker 1': 'Bob'}
  
    directory = os.path.join(current_app.root_path, 'transcription/files/transcripts')
    file_path = os.path.join(directory, filename)
  
    # Ensure the transcript file exists
    if not os.path.isfile(file_path):
        return jsonify({"error": "Transcript file not found"}), 404
  
    # Read, update, and save the transcript with actual speaker names
    try:
        with open(file_path, "r+") as file:
            transcript_data = file.read()
            for placeholder, actual_name in speaker_names.items():
                transcript_data = transcript_data.replace(placeholder, actual_name)
            
            file.seek(0)
            file.write(transcript_data)
            file.truncate()  # Truncate file to new size if it got shorter
  
    except Exception as e:
        return jsonify({"error": "Error updating transcript", "details": str(e)}), 500
  
    # Return the updated file
    return send_from_directory(directory=directory, path=filename, as_attachment=True)