from flask import Blueprint, request, jsonify, send_file, current_app, send_from_directory
from werkzeug.utils import secure_filename
import os
import requests
import whisper
from datetime import datetime

transcript_bp = Blueprint("transcript_bp", __name__)


from . import routes

TRANSCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")

# Load the Whisper model
model = whisper.load_model("large")  # Choose between "tiny", "base", "small", "medium", "large" based on your needs and resources

def speaker_diarization(file_path):
  # Replace YOUR_DEEPGRAM_API_KEY with your actual API key
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
      return speaker_details
  else:
      print(f"Error: API request failed with status code {response.status_code}")

def perform_asr(file_path):
    # Transcribe the audio file with timestamps
    audio_file_path = file_path  # Replace with the path to your actual file
    result_segments = model.transcribe(audio_file_path)

    # Initialize a list to hold transcription details
    transcription_details = []

    # The result contains a 'segments' key with the transcription and timestamps for each segment
    for segment in result_segments["segments"]:
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
        trans_duration = trans_end - trans_start 
        
        # Ensure the trans_duration is at least 0.1 seconds to prevent division by zero
        trans_duration = max(trans_duration, 0.1)
        
        return overlap_duration / trans_duration  # This is the overlap percentage

    # For each transcript segment, determine the corresponding speaker
    for transcription in transcription_details:
        trans_start = round(transcription['start_time'], 1)
        trans_end = round(transcription['end_time'], 1)

        # Generate list of (speaker, overlap_percentage) tuples
        overlap_list = [
            (speaker['speaker_id'], calculate_overlap(speaker, trans_start, trans_end)) 
            for speaker in speaker_results
        ]

        # Sort by overlap percentage in descending order
        overlap_list.sort(key=lambda x: -x[1])
        
        # Determine the speakers for this segment
        chosen_speakers = set()  # Use a set for unique speaker IDs
        for speaker_id, overlap_percentage in overlap_list:
            if overlap_percentage >= 0.8:
                chosen_speakers.add(speaker_id)
                break
            elif overlap_percentage > 0:
                chosen_speakers.add(speaker_id)

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
                speaker_lines[speaker_id].append(transcription['text'])  # Add text to speaker
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

        # Helper function to write segments to the file
        def write_segment(speaker_ids, start_time, end_time, texts):
            speaker_label = " & ".join(f"Speaker {speaker_id}" for speaker_id in speaker_ids)
            # Write combined speaker/timestamp line
            file.write(f"[{speaker_label}] ({start_time}s - {end_time}s):\n")
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
                    write_segment(previous_speaker_ids, segment_start_time, end_time, segment_texts)
                
                # Reset for the new speaker segment
                previous_speaker_ids = segment['speaker_ids']
                segment_start_time = segment['start_time']
                end_time = segment['end_time']
                segment_texts = [segment['text']]

        # Make sure to write the last segment
        if previous_speaker_ids is not None:
            write_segment(previous_speaker_ids, segment_start_time, end_time, segment_texts)

    return filename

def allowed_file(filename):
  ALLOWED_EXTENSIONS = {'mp3', 'wav'}
  
  # Split the filename by '.' and check if the last part (extension) is in the allowed set
  return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@transcript_bp.route("/mp3", methods=["POST"])
def init_transcription():
  if 'audio_input' not in request.files:
      return jsonify({"error": "No file part"}), 400
  
  audio_file = request.files['audio_input']
  
  if audio_file.filename == '':
      return jsonify({"error": "No selected file"}), 400
  
  if audio_file and allowed_file(audio_file.filename):
      filename = secure_filename(audio_file.filename)
      
      # Create a unique or specific directory for audio files if doesn't exist
      audio_file_path = os.path.join(current_app.root_path, 'uploaded_files', filename)
      os.makedirs(os.path.dirname(audio_file_path), exist_ok=True)
        
  # Save file to the server
  audio_file.save(audio_file_path)
  speaker_results = speaker_diarization(audio_file_path)
  transcription_details = perform_asr(audio_file_path)
  full_transcript, speaker_summaries = combine_speaker_and_transcription(speaker_results, transcription_details)
  transcript_file = display_transcript(full_transcript)

  response = {
    "filename": transcript_file,
    "speakers": speaker_summaries
  }

  return jsonify(response), 200

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