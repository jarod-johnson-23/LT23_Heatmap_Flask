from flask import Blueprint, request, jsonify, send_file, current_app, send_from_directory, Response
from werkzeug.utils import secure_filename
import os
import requests
import re
from datetime import datetime
from collections import defaultdict
from openai import OpenAI
from pydub import AudioSegment
from tempfile import NamedTemporaryFile
import subprocess

transcript_bp = Blueprint("transcript_bp", __name__)


from . import routes

TRANSCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")

client = OpenAI(
    organization=os.getenv("openai_organization"),
    api_key=os.getenv("openai_api_key"),
)

def log_to_file(message):
    log_file_path = os.path.join(current_app.root_path, 'transcription/logs/log.txt')
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    with open(log_file_path, 'a') as log_file:
        log_file.write(f"{datetime.now()}: {message}\n")

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
      log_to_file(speaker_details)
      return speaker_details
  else:
      print(f"Error: API request failed with status code {response.status_code}")

MAX_FILE_SIZE = 26214400  # 26 MB in bytes

def perform_asr(file_path, prompt=""):
    # Check the file size
    file_size = os.path.getsize(file_path)
    
    # If the file is small enough, process it in one go.
    if file_size <= MAX_FILE_SIZE:
        with open(file_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                file=audio_file,
                model="whisper-1",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
                language="en",
            )
        return [
            {
                'start_time': segment['start'],
                'end_time': segment['end'],
                'text': segment['text']
            }
            for segment in response.segments
        ]
    
    # Otherwise, load the audio using pydub to split it.
    audio = AudioSegment.from_file(file_path)
    duration_ms = len(audio)  # duration in milliseconds
    
    # Determine a safe chunk duration.
    # One way is to calculate the average bytes per millisecond in the file,
    # then find how many milliseconds we can have before hitting MAX_FILE_SIZE.
    bytes_per_ms = file_size / duration_ms
    chunk_duration_ms = int(MAX_FILE_SIZE / bytes_per_ms)
    
    # (Optional) You might want to use a fixed maximum duration if you know your files
    # are encoded at similar bitrates. For example:
    # chunk_duration_ms = min(chunk_duration_ms, 60000)  # maximum 60 seconds per chunk
    
    transcription_details = []
    
    # Process each chunk individually.
    for chunk_start in range(0, duration_ms, chunk_duration_ms):
        # Define the chunk boundaries
        chunk_end = min(chunk_start + chunk_duration_ms, duration_ms)
        audio_chunk = audio[chunk_start:chunk_end]
        
        # Export the chunk to a temporary file
        with NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_filename = tmp.name
            # Export as WAV (or use the format that your API expects)
            audio_chunk.export(tmp_filename, format="wav")
        
        # Call the transcription API for the chunk.
        with open(tmp_filename, "rb") as chunk_file:
            response = client.audio.transcriptions.create(
                file=chunk_file,
                model="whisper-1",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
                language="en",
            )
        # Clean up the temporary file.
        os.remove(tmp_filename)
        
        # Adjust segment times by adding the chunk's offset (converted from ms to seconds)
        offset_sec = chunk_start / 1000.0
        for segment in response.segments:
            transcription_details.append({
                'start_time': segment['start'] + offset_sec,
                'end_time': segment['end'] + offset_sec,
                'text': segment['text']
            })
    
    # Return the combined transcription details in the same formatting.
    return transcription_details


def combine_speaker_and_transcription(speaker_results, transcription_details):
    combined_transcript = []
    speaker_lines = {}  # To hold the lines spoken by each speaker

    def calculate_overlap(speaker_start, speaker_end, trans_start, trans_end):
        overlap_start = max(speaker_start, trans_start)
        overlap_end = min(speaker_end, trans_end)
        overlap_duration = max(0, overlap_end - overlap_start)
        return overlap_duration

    for transcription in transcription_details:
        trans_start = transcription['start_time']
        trans_end = transcription['end_time']

        # Normalize the speaker segments based on the current transcription start time
        normalized_speaker_results = [
            {
                'speaker_id': speaker['speaker_id'],
                'start_timestamp': speaker['start_timestamp'] - trans_start,
                'end_timestamp': speaker['end_timestamp'] - trans_start
            }
            for speaker in speaker_results
        ]

        # Normalize the transcription segment so it starts at 0
        normalized_trans_start = 0
        normalized_trans_end = trans_end - trans_start

        # Generate list of (speaker, overlap_time) tuples
        overlap_list = [
            (
                speaker['speaker_id'], 
                calculate_overlap(
                    speaker['start_timestamp'], 
                    speaker['end_timestamp'], 
                    normalized_trans_start, 
                    normalized_trans_end
                )
            )
            for speaker in normalized_speaker_results
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
                'start_time': transcription['start_time'],  # Use original (unnormalized) time for output
                'end_time': transcription['end_time'],  # Use original (unnormalized) time for output
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

def display_transcript(transcript_data, base_filename):
    # Generate the output filename based on the input audio file
    transcript_filename = f"{base_filename}_transcript.txt"
    
    transcript_file_path = os.path.join(current_app.root_path, 'transcription/files/transcripts', transcript_filename)
    
    with open(transcript_file_path, 'w') as file:
        # Initialize previous speaker and start time for combining segments
        previous_speaker_ids = None
        segment_start_time = None
        segment_texts = []

        def write_segment(file, speaker_ids, start_time, end_time, texts):
            speaker_label = " & ".join(f"Speaker {speaker_id}" for speaker_id in speaker_ids)
                
            def format_time(seconds):
                minutes = int(seconds) // 60
                seconds = int(seconds % 60)  # Convert to integer and remove decimals
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

    return transcript_filename

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'mp3', 'wav', 'mp4', 'm4a', 'aac', 'ogg'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def convert_to_mp3(file_path, filename):
    try:
        # Determine the input format
        file_extension = os.path.splitext(filename)[1].lower()
        
        # Load the file into AudioSegment based on extension
        if file_extension == '.mp4':
            audio = AudioSegment.from_file(file_path, format='mp4')
        else:
            audio = AudioSegment.from_file(file_path)
        
        # Create an MP3 filename
        mp3_filename = f"{os.path.splitext(filename)[0]}.mp3"
        mp3_file_path = os.path.join(os.path.dirname(file_path), mp3_filename)

        # Export as MP3
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
        original_filename = secure_filename(audio_file.filename)
        # Extract the base name (without extension) and append "transcript"
        base_filename = os.path.splitext(original_filename)[0]

        # Create a unique or specific directory for audio files if doesn't exist
        audio_file_path = os.path.join(current_app.root_path, 'transcription/files/audio', original_filename)
        os.makedirs(os.path.dirname(audio_file_path), exist_ok=True)

        # Save the uploaded file
        audio_file.save(audio_file_path)

        # Convert to MP3 if necessary
        if not original_filename.endswith('.mp3'):
            audio_file_path = convert_to_mp3(audio_file_path, original_filename)
            if audio_file_path is None:
                return jsonify({"error": "Failed to convert file to MP3"}), 400

        speaker_results = speaker_diarization(audio_file_path)
        transcription_details = perform_asr(audio_file_path, prompt)
        full_transcript, speaker_summaries = combine_speaker_and_transcription(speaker_results, transcription_details)
        
        # Call the display_transcript function to format and save the transcript
        transcript_filename = display_transcript(full_transcript, base_filename)

        try:
            os.remove(audio_file_path)
        except OSError as e:
            print(f"Error: {audio_file_path} : {e.strerror}")

        return jsonify({"message": transcript_filename, "summaries": speaker_summaries}), 200
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
        if len(text) > 300:
            truncated_text = text[:300].rsplit(' ', 1)[0] + '...'
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
                # Ensure the placeholder format matches the exact text in the transcript
                transcript_data = transcript_data.replace(f"[{placeholder}]", f"[{actual_name}]")
            
            file.seek(0)
            file.write(transcript_data)
            file.truncate()

    except Exception as e:
        return jsonify({"error": "Error updating transcript", "details": str(e)}), 500
  
    # Return the updated file
    return send_from_directory(directory=directory, path=filename, as_attachment=True)