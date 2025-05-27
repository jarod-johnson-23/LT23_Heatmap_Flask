import os
import json
import requests
import pandas as pd
from flask import Blueprint, jsonify, request
from openai import OpenAI
from scipy.spatial.distance import cosine

##############################
# Flask Blueprint
##############################
audiobot_bp = Blueprint("audiobot_bp", __name__)
from . import routes

##############################
# Initialize OpenAI client
##############################
client = OpenAI(
    organization=os.getenv("openai_organization"),
    api_key=os.getenv("openai_api_key"),
)

##############################
# Configuration
##############################
EMBEDDING_MODEL = "text-embedding-3-large"  # Replace with a valid model name

##############################
# Load Embeddings at Startup
##############################
def load_embeddings(csv_path="pdf_embeddings.csv"):
    """
    Loads CSV containing columns: 
      [filename, chunk_index, text_chunk, embedding (JSON-encoded)]
    Converts the 'embedding' column from JSON to a list of floats.
    """
    df = pd.read_csv(csv_path)
    df["embedding"] = df["embedding"].apply(json.loads)
    return df

# Load once globally (avoid re-loading on every request)
EMBEDDINGS_DF = load_embeddings("./app/audiobot/embeddings/pdf_embeddings.csv")

##############################
# Utility Functions
##############################
def get_embedding(text, model=EMBEDDING_MODEL):
    """
    Gets the embedding for a single string using the same style as the previous code.
    """
    # This returns a list of floats for the first (and only) item in the response
    response = client.embeddings.create(
        input=[text],
        model=model
    )
    return response.data[0].embedding


def rank_strings_by_relatedness(query, df, top_n=8):
    """
    Given a query, compute the cosine similarity for each row in df,
    and return the top N rows (sorted by similarity descending).
    """
    query_embedding = get_embedding(query)

    def cosine_similarity(a, b):
        # 1 - cosine_distance = cosine_similarity
        return 1 - cosine(a, b)
    
    similarities = []
    for idx, row in df.iterrows():
        chunk_embedding = row["embedding"]
        similarities.append(cosine_similarity(query_embedding, chunk_embedding))
    
    df["similarity"] = similarities
    df_sorted = df.sort_values("similarity", ascending=False).head(top_n)
    return df_sorted


@audiobot_bp.route("/session", methods=["POST"])
def create_session():
    """
    Creates an OpenAI Realtime API session with customizable instructions and voice.
    Accepts JSON payload with 'instructions' and 'voice' parameters.
    """
    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {os.getenv('openai_api_key')}",
        "Content-Type": "application/json",
    }

    # Get JSON data from the request
    data = request.get_json()

    # Extract instructions and voice, provide defaults if missing
    instructions = data.get("instructions", "You are a helpful assistant.")
    voice = data.get("voice", "nova")  # Default voice if not provided

    payload = {
        "model": "gpt-4o-realtime-preview-2024-12-17",
        "instructions": instructions,
        "voice": voice,
        "input_audio_transcription": {
            "model": "whisper-1"
        },
        "tools": [{
            "type": "function",
            "name": "search_files",
            "description": "This is a search function that the Thermo King service support agent uses to search a knowledgebase of service manuals and technical briefings. This function accepts a search string that best represents the information the service agent needs to help the service technician.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "The string that is used to perform the search across service manuals and briefings.",
                    }
                },
                "required": ["search_term"]
            }
        }],
        "tool_choice": "auto",
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return jsonify(data)
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 500


##############################
# Search Similar Sections
##############################
@audiobot_bp.route("/search_sections", methods=["GET"])
def search_sections():
    """
    Takes a query (via GET param ?query=...) and returns the top 8 most similar PDF text chunks.
    """
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"error": "Missing or empty 'query' parameter"}), 400

    # Get top 8 most similar chunks
    top_matches = rank_strings_by_relatedness(query, EMBEDDINGS_DF, top_n=8)

    # Build a JSON-serializable list
    results = []
    for _, row in top_matches.iterrows():
        results.append({
            "filename": row["filename"],
            "chunk_index": int(row["chunk_index"]),
            "similarity": float(row["similarity"]),
            "text_chunk": row["text_chunk"],
        })

    return jsonify({"results": results})
