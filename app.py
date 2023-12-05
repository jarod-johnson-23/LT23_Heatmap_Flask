import os
import io
import uuid
import base64
from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    send_file,
    make_response,
    Blueprint,
)
from openai import OpenAI
import pandas as pd
import geopandas as gpd
import folium
from folium import Choropleth
import simplekml
import json
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from openpyxl.styles import NamedStyle
from pymongo import MongoClient
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
import boto3
from botocore.exceptions import ClientError
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity,
)
from itsdangerous import SignatureExpired, BadSignature, URLSafeTimedSerializer
from dotenv import load_dotenv
from datetime import timedelta
from flask_cors import CORS

ai_client = OpenAI(
    organization=os.getenv("openai_organization"), api_key=os.getenv("openai_api_key")
)

app = Flask(__name__)
load_dotenv()
CORS(
    app,
    resources={r"/*": {"origins": os.getenv("base_url_react")}},
    supports_credentials=True,
)
bcrypt = Bcrypt(app)

# Configure Flask-PyMongo
mongo_uri = os.getenv("mongo_uri")
client = MongoClient(mongo_uri)
db = client["LT-db-dashboard"]
user_collection = db["userInfo"]
user_collection.create_index("email", unique=True)

app.config["TOKEN_KEY"] = os.getenv("TOKEN_KEY")


def generate_jwt_secret_key(length=64):
    # Generate random bytes
    random_bytes = os.urandom(length)
    # Base64 encode the bytes to create a URL-safe secret key
    secret_key = base64.urlsafe_b64encode(random_bytes).decode("utf-8")
    return secret_key


app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=1)
app.config["JWT_SECRET_KEY"] = generate_jwt_secret_key()
jwt = JWTManager(app)
serializer = URLSafeTimedSerializer(app.config["TOKEN_KEY"])


@app.route("/admin/create-user", methods=["POST"])
def admin_create_user():
    email = request.json.get("email")
    access = request.json.get("accessRights")  # Presuming the admin sends this

    # Check if email was provided
    if not email:
        return jsonify({"error": "Email is required"}), 400

    try:
        user = {
            "email": email,
            "access": access,
            "setupComplete": False,  # Indicates the user has not completed the setup
        }

        # Insert the user into the database
        result = user_collection.insert_one(user)
        # TODO: Trigger email to user with the account setup link

        token = serializer.dumps(email, salt=os.getenv("salt"))

        # Create a link to the account creation page with the token
        link = f"{os.getenv('base_url_react')}/create-account/{token}"

        # Email content with the link
        email_body = f"""To complete your sign up and gain access, simply click on the following link and follow the instructions to create your account: {link}\nPlease note that this invitation link is uniquely tied to your email address, sharing it with others will result in an account under your email address. If you need to use a different email address, please contact the DEV team. The invitation link will expire in 10 hours."""

        aws_region = "us-east-2"

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
                    "ToAddresses": [email],
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
                        "Data": "LT Web Service Dashboard Invitation",
                    },
                },
                Source="no-reply@laneterraleverapi.org",  # Your verified address
            )
        except ClientError as e:
            print(f"An error occurred: {e.response['Error']['Message']}")
        else:
            print(f"Email sent! Message ID: {response['MessageId']}")
        user_id = result.inserted_id

        # Return the ObjectId as a string in the response
        # Since ObjectId is not JSON serializable, we convert it to string
        return jsonify({"msg": link, "_id": str(user_id)}), 201

    except DuplicateKeyError:
        return jsonify({"error": "Duplicate email"}), 409
    except Exception as e:
        print(e)
        return jsonify({"error": str(e)}), 400


@app.route("/user/register", methods=["POST"])
def user_complete_setup():
    email = request.json.get("email")
    password = request.json.get("password")
    first_name = request.json.get("firstName", "")
    last_name = request.json.get("lastName", "")
    role = request.json.get("role", "")

    # Validate that the required email and password have been provided
    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    try:
        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")

        update_result = user_collection.update_one(
            {"email": email, "setupComplete": False},
            {
                "$set": {
                    "password": hashed_password,
                    "firstName": first_name,
                    "lastName": last_name,
                    "role": role,
                    "setupComplete": True,  # Mark the setup as complete
                }
            },
        )

        if update_result.matched_count == 0:
            return jsonify({"error": "No user found or setup already complete"}), 404

        access_token = create_access_token(identity=email)
        return jsonify({"msg": "Account setup complete", "token": access_token}), 200

    except DuplicateKeyError:
        return jsonify({"error": "Duplicate email"}), 409
    except Exception as e:
        print(e)
        return jsonify({"error": str(e)}), 400


@app.route("/user/login", methods=["POST"])
def login():
    email = request.json.get("email")
    password = request.json.get("password")
    if not email:
        return jsonify({"error": "Email is required"}), 400
    if not password:
        return jsonify({"error": "Password is required"}), 400
    user = user_collection.find_one({"email": email})
    if user and bcrypt.check_password_hash(user["password"], password):
        access_token = create_access_token(identity=email)
        return jsonify(access_token=access_token, id=str(user["_id"])), 200
    else:
        return jsonify({"msg": "Bad email or password"}), 401


@app.route("/get_access", methods=["POST"])
def get_access():
    data = request.json
    email = data.get("email")

    if not email:
        return jsonify({"message": "Email is required."}), 400

    # Query the database
    result = user_collection.find_one({"email": email}, {"access": 1, "_id": 0})

    # Check if a result was found
    if result:
        return jsonify(result), 200
    else:
        return jsonify({"message": "No user found with that email."}), 404


@app.route("/verify-token/<token>", methods=["GET"])
def verify_token(token):
    try:
        email = serializer.loads(
            token,
            salt=os.getenv("salt"),
            max_age=36000,  # Token expires after 10 hours
        )
    except SignatureExpired:
        return jsonify({"error": "Token expired"}), 400
    except BadSignature:
        return jsonify({"error": "Invalid token"}), 400

    # Token is valid, continue with the account creation process
    return jsonify({"message": "Token is valid", "email": email}), 200


@app.route("/users", methods=["GET"])
def get_all_users():
    try:
        # Query all user documents excluding the "password" field
        users = user_collection.find({}, {"password": 0})
        user_list = list(users)

        # Convert the ObjectId fields to strings to make them JSON serializable
        for user in user_list:
            user["_id"] = str(user["_id"])

        return jsonify(user_list), 200
    except Exception as e:
        print(e)
        return jsonify({"error": "An error occurred fetching the user data."}), 500


@app.route("/users/<user_id>/update-access", methods=["PATCH"])
def update_user_access(user_id):
    try:
        access_rights = request.json.get("access")
        result = user_collection.update_one(
            {"_id": ObjectId(user_id)}, {"$set": {"access": access_rights}}
        )

        if result.matched_count == 0:
            return jsonify({"error": "No user found with provided ID."}), 404
        elif result.modified_count == 0:
            return jsonify({"error": "User access rights not updated."}), 304
        else:
            return jsonify({"message": "User access rights updated successfully."}), 200
    except Exception as e:
        print(e)
        return (
            jsonify({"error": "An error occurred updating the user access rights."}),
            500,
        )


@app.route("/users/<user_id>/delete", methods=["DELETE"])
def delete_user(user_id):
    try:
        result = user_collection.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count == 0:
            return jsonify({"error": "User not found."}), 404
        else:
            return jsonify({"message": "User deleted successfully."}), 200
    except Exception as e:
        print(e)
        return (
            jsonify({"error": "An error occurred while trying to delete the user."}),
            500,
        )


@app.route("/protected", methods=["GET"])
@jwt_required()
def protected():
    current_user = get_jwt_identity()
    return jsonify({"logged_in_as": current_user}), 200


# Directory to save the generated heatmaps
HEATMAP_DIR = "./heatmap/result"

ALLOWED_EXTENSIONS = {"xls", "xlsx", "csv"}

STATE_DICT = {
    "AL": "alabama",
    "AK": "alaska",
    "AZ": "arizona",
    "AR": "arkansas",
    "CA": "california",
    "CO": "colorado",
    "CT": "connecticut",
    "DE": "delaware",
    "FL": "florida",
    "GA": "georgia",
    "HI": "hawaii",
    "ID": "idaho",
    "IL": "illinois",
    "IN": "indiana",
    "IA": "iowa",
    "KS": "kansas",
    "KY": "kentucky",
    "LA": "louisiana",
    "ME": "maine",
    "MD": "maryland",
    "MA": "massachusetts",
    "MI": "michigan",
    "MN": "minnesota",
    "MS": "mississippi",
    "MO": "missouri",
    "MT": "montana",
    "NE": "nebraska",
    "NV": "nevada",
    "NH": "new hampshire",
    "NJ": "new jersey",
    "NM": "new mexico",
    "NY": "new york",
    "NC": "north carolina",
    "ND": "north dakota",
    "OH": "ohio",
    "OK": "oklahoma",
    "OR": "oregon",
    "PA": "pennsylvania",
    "RI": "rhode island",
    "SC": "south carolina",
    "SD": "south dakota",
    "TN": "tennessee",
    "TX": "texas",
    "UT": "utah",
    "VT": "vermont",
    "VA": "virginia",
    "WA": "washington",
    "WV": "west virginia",
    "WI": "wisconsin",
    "WY": "wyoming",
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_input(input_str):
    if input_str:
        return list(map(int, input_str.split(",")))
    else:
        return []


def preprocess_data(filename, exclude_index=0):
    # Determine the file type and read accordingly
    if filename.endswith(".xlsx"):
        df = pd.read_excel(filename)
    elif filename.endswith(".csv"):
        df = pd.read_csv(filename)
    else:
        raise ValueError("Unsupported file format")

    # Get the column name based on the provided index
    exclude_column = df.columns[exclude_index] if exclude_index is not None else None

    # Iterate over all values in the dataframe, excluding the specified column
    for col in df.columns:
        if col != exclude_column:
            df[col] = df[col].apply(lambda x: convert_to_number(x))

    return df


def convert_to_number(val):
    # If value is a string, remove commas
    if isinstance(val, str):
        val = val.replace(",", "")

    # Try converting to a number
    try:
        number = float(val)
        return number
    except ValueError:
        return "0"


# Step 1: Modify Media Name and Budget Code
def modify_media_and_budget(toyota_data, YC):
    toyota_data["Media Name"] = toyota_data["Media Name"].replace(
        {"Cable": "Broadcast", "TV": "Broadcast", "Radio": "Radio/Online Radio"}
    )
    toyota_data["Budget Code"] = toyota_data["Budget Code"].replace(
        {"Event": f"TDA{YC}", "TCUV": f"TCUV{YC}", "Parts and Service": f"PS{YC}"}
    )
    return toyota_data


# Step 2: Update the Campaign Column
def update_campaign(toyota_data, YC, MC):
    toyota_data["Campaign"] = "RYO" + YC + MC  # Default value
    toyota_data.loc[toyota_data["Budget Code"] == f"PS{YC}", "Campaign"] = f"PS{YC}{MC}"
    toyota_data.loc[
        toyota_data["Budget Code"] == f"TCUV{YC}", "Campaign"
    ] = f"TCUV{YC}{MC}"
    toyota_data["Campaign"] = toyota_data["Campaign"].str.replace(
        " ", ""
    )  # Removing any spaces
    return toyota_data


# Step 3: Update the Diversity Column
def update_diversity(toyota_data):
    diversity_keywords = [
        "Entravision",
        "KFPH-S2",
        "KHOT",
        "KLNZ",
        "KNAI",
        "KOMR",
        "KQMR",
        "KTVW",
        "KTAZ",
        "KVVA",
        "Univision",
    ]
    toyota_data["Diversity"] = "General"  # Default value
    toyota_data.loc[
        toyota_data["Activity Description"].str.contains(
            "|".join(diversity_keywords), case=False, na=False
        ),
        "Diversity",
    ] = "Hispanic"
    return toyota_data


# Step 4: Modify Dates, Vehicle Series Name, and Claimed Amount
def modify_dates_and_amounts(toyota_data, coop_data, nielsen_data, YC, MC):
    new_rows = []
    for index, row in toyota_data.iterrows():
        if row["Vehicle Series Name"] != "Brand":
            coop_matching_row = coop_data[
                (coop_data["Budget Code"] == row["Budget Code"])
                & (coop_data["Media Name"] == row["Media Name"])
            ]
            if not coop_matching_row.empty:
                coop_matching_row = coop_matching_row.iloc[0]
                for category, percentage in coop_matching_row.items():
                    if category not in ["Budget Code", "Media Name"] and percentage > 0:
                        new_row = row.copy()
                        new_row["Vehicle Series Name"] = category
                        new_row["Claimed Amount"] = row["Activity Cost"] * percentage
                        new_rows.append(new_row)
                toyota_data = toyota_data.drop(index)

    # Update the Activity Start Date and Activity End Date based on the Nielsen Calendar data
    nielsen_calendar = nielsen_data[
        (nielsen_data["Month"] == int(MC)) & (nielsen_data["Year"] == int(YC))
    ]
    for index, row in toyota_data.iterrows():
        toyota_data.at[index, "Activity Start Date"] = (
            nielsen_calendar["Start Date"].dt.strftime("%m-%d-%Y").values[0]
        )
        toyota_data.at[index, "Activity End Date"] = (
            nielsen_calendar["End Date"].dt.strftime("%m-%d-%Y").values[0]
        )
        toyota_data.at[index, "Claimed Amount"] = toyota_data.at[index, "Activity Cost"]
        toyota_data.at[index, "Vehicle Series Name"] = "Brand"

    new_rows_df = pd.DataFrame(new_rows)
    toyota_data = pd.concat([toyota_data, new_rows_df], ignore_index=True)

    return toyota_data


def save_to_excel(toyota_data, output):
    # Step 1: Save the DataFrame to an Excel file stored in a BytesIO object
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        toyota_data.to_excel(writer, index=False)
        wb = writer.book
        ws = writer.sheets["Sheet1"]

        # Step 2: Apply currency formatting
        currency_style = NamedStyle(name="currency", number_format="$#,##0.00")
        wb.add_named_style(currency_style)

        for row in ws.iter_rows(
            min_row=2, max_row=ws.max_row, min_col=7, max_col=9
        ):  # Columns G to I
            for cell in row:
                cell.style = currency_style

    # The workbook is saved when exiting the context manager
    output.seek(0)


# Load the data
def load_data():
    nielsen_data = pd.read_excel("./toyota/files/NielsenCalendar.xlsx")
    return nielsen_data


@app.route("/")
def index():
    return {"STATUS": "OK", "CODE": 200}


@app.route("/toyota_media_buy_processing", methods=["POST"])
def toyota_media_buy_processing():
    try:
        YC = request.form.get("YC")
        MC = request.form.get("MC")

        toyota_file = request.files.get("toyota_file")
        coop_file = request.files.get("coop_file")

        if toyota_file and coop_file and YC and MC:
            toyota_filename = secure_filename(toyota_file.filename)
            coop_filename = secure_filename(coop_file.filename)

            toyota_filepath = os.path.join("./toyota/files", toyota_filename)
            coop_filepath = os.path.join("./toyota/files", coop_filename)

            toyota_file.save(toyota_filepath)
            coop_file.save(coop_filepath)

            nielsen_data = load_data()
            toyota_data = pd.read_excel(toyota_filepath, skiprows=1)
            coop_data = pd.read_excel(coop_filepath).drop(
                columns=["Total"], errors="ignore"
            )
            toyota_data = modify_media_and_budget(toyota_data, YC)
            toyota_data = update_campaign(toyota_data, YC, MC)
            toyota_data = update_diversity(toyota_data)
            toyota_data = modify_dates_and_amounts(
                toyota_data, coop_data, nielsen_data, YC, MC
            )

            os.remove(toyota_filepath)
            os.remove(coop_filepath)

            # file_path = "./toyota/files/toyota_data.xlsx"
            # toyota_data.to_excel(file_path, index=False, engine="openpyxl")

            output = io.BytesIO()
            save_to_excel(toyota_data, output)

            # Send the Excel file to the user
            return send_file(
                output,
                as_attachment=True,
                download_name="toyota_data.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            return jsonify({"error": "File Error(s)"}), 412
    except Exception as e:
        # Log the exception for debugging
        app.logger.error(f"An error occurred: {str(e)}")

        # Return a JSON response indicating an error
        return jsonify({"error": "An error occurred", "message": str(e)}), 500


@app.route("/ai_prompt_download")
def download_file():
    file_path = "./ai_prompt_download/AI_Prompt_Files.zip"
    return send_file(file_path, as_attachment=True)


@app.route("/heatmap/result/<filename>")
def serve_file(filename):
    # Modify the function to serve both HTML and KML files with content type checks
    # Note: Better to have separate endpoints or a middleware that handles different file types
    if ".." in filename or filename.startswith("/"):
        return "Invalid path!", 400

    file_path = os.path.join(HEATMAP_DIR, filename)
    if not os.path.exists(file_path):
        return "File not found!", 404

    # Infer content type from file extension
    if filename.endswith(".html"):
        content_type = "text/html"
    elif filename.endswith(".kml"):
        content_type = "application/vnd.google-earth.kml+xml"
    else:
        return "Invalid file type!", 400

    return send_from_directory(HEATMAP_DIR, filename, mimetype=content_type)


def generate_kml(geojson_data, col_name, other_cols, max_value, min_value, file_prefix):
    kml = simplekml.Kml()

    for feature in geojson_data["features"]:
        # Zip code polygons represented as 'MultiPolygon' or 'Polygon' in GeoJSON
        poly_geojson = feature["geometry"]
        if poly_geojson["type"] == "MultiPolygon":
            for polygon in poly_geojson["coordinates"]:
                create_kml_polygon(
                    kml, polygon, feature, col_name, other_cols, max_value, min_value
                )
        elif poly_geojson["type"] == "Polygon":
            create_kml_polygon(
                kml,
                poly_geojson["coordinates"],
                feature,
                col_name,
                other_cols,
                max_value,
                min_value,
            )

    kml_file_path = os.path.join(HEATMAP_DIR, f"{file_prefix}_{uuid.uuid4().hex}.kml")
    kml.save(kml_file_path)
    return kml_file_path


def create_kml_polygon(
    kml, polygon, feature, col_name, other_cols, max_value, min_value
):
    poly = kml.newpolygon(name=feature["properties"]["ZCTA5CE10"])
    # Now let's add a BalloonStyle
    # Start an HTML table with the column names and values
    # balloon_html = """
    # <style type='text/css'>
    #     .balloon-table {{
    #         width: 100%;
    #         border-collapse: collapse;
    #         font-family: 'Arial', sans-serif;
    #     }}
    #     .balloon-table th,
    #     .balloon-table td {{
    #         border: 1px solid #ddd;
    #         padding: 8px;
    #         text-align: left;
    #     }}
    #     .balloon-table th {{
    #         background-color: #f2f2f2;
    #         color: #333;
    #     }}
    #     .balloon-table tr:nth-child(even) {{
    #         background-color: #f9f9f9;
    #     }}
    # </style>
    # <p><b>Zip Code:</b> {zip_code}</p>
    # <table class='balloon-table'>
    #     <tr>
    #         <th>{main_col_name}</th>
    #         <td>{main_col_value}</td>
    #     </tr>
    # """.format(
    #     zip_code=feature["properties"]["ZCTA5CE10"],
    #     main_col_name=col_name,
    #     main_col_value=feature["properties"][col_name],
    # )

    # Add rows for any additional columns
    # for col in other_cols:
    #     col_value = feature["properties"].get(
    #         col, "N/A"
    #     )  # Use 'N/A' if value is missing
    #     balloon_html += """
    #     <tr>
    #         <td><b>{col_name}</b></td>
    #         <td>{col_value}</td>
    #     </tr>
    #     """.format(
    #         col_name=col, col_value=col_value
    #     )

    # # Close the HTML table
    # balloon_html += "</table>"
    balloon_text = f"{col_name}: {feature['properties'][col_name]}\n"

    balloon_text += "\n".join(
        [f"{col_name}: {feature['properties'][col_name]}" for col_name in other_cols]
    )
    poly.description = balloon_text

    # Assign the balloon text to the BalloonStyle
    # poly.style.balloonstyle.text = balloon_html
    # Convert coordinates to the correct format
    kml_coordinates = convert_coordinates_to_kml(polygon)
    poly.outerboundaryis = kml_coordinates
    value = feature["properties"][col_name]
    poly.style.polystyle.color = simplekml.Color.changealphaint(
        175, color_scale(value, max_value, min_value, kml=True)
    )
    poly.style.polystyle.outline = 3
    # You can set altitude mode here if needed, for example:
    # poly.altitudemode = simplekml.AltitudeMode.clamptoground


# Modify color_scale to support KML color generation
def color_scale(value, max_value, min_value, kml=False):
    diff = max_value - min_value
    if value is None:
        color = "#ffffff00"
    elif value == 0:
        color = "#ffffff00"
    elif value < (diff * 0.1) + min_value:
        color = "#ffffb2"
    elif value < (diff * 0.25) + min_value:
        color = "#feda76"
    elif value < (diff * 0.4) + min_value:
        color = "#f5b156"
    elif value < (diff * 0.55) + min_value:
        color = "#f59356"
    elif value < (diff * 0.7) + min_value:
        color = "#f07f64"
    elif value < (diff * 0.8) + min_value:
        color = "#ec5b45"
    elif value < (diff * 0.9) + min_value:
        color = "#e73727"
    else:
        color = "#d7191c"

    # Convert hex color to KML format if kml parameter is true
    if kml:
        return html_color_to_kml_color(color)
    return color


def html_color_to_kml_color(html_color, alpha="22"):
    # Assume html_color is of the form "#rrggbb".
    # strip the leading '#' and add alpha opacity value which is at the beginning in KML color codes
    kml_color = alpha + html_color[5:7] + html_color[3:5] + html_color[1:3]
    return kml_color


def convert_coordinates_to_kml(polygon):
    # GeoJSON coordinates are in [longitude, latitude] order, may include altitude
    # KML expects a list of tuples in (longitude, latitude[, altitude]) order
    # Assuming polygon is a list of lists where each inner list represents a point
    # In GeoJSON, polygons are an array of LinearRings (first is outer, rest are holes)
    # For simplicity, this function only converts the outer boundary

    outer_boundary = polygon[0]  # Get the outer boundary (ignore holes)
    return [
        (lon, lat) for lon, lat in outer_boundary
    ]  # KML has only lon, lat for LinearRing


def get_column_names_from_indices(df, sec_col_input):
    # Split the input string into a list of indices
    indices = [int(x.strip()) for x in sec_col_input.split(",")]

    # Convert indices to column names, assuming 1-based indices from user input
    column_names = [df.columns[index] for index in indices]

    return column_names


@app.route("/heatmap/zipcode", methods=["POST"])
def generate_heatmap():
    if "excel_file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["excel_file"]

    # Check if user did not select file
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    # Check if file is allowed
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join("./heatmap/data", filename)
        file.save(filepath)
        # Now, use the saved file path in your existing code
        excel_path = filepath

        input_df = preprocess_data(
            excel_path, exclude_index=int(request.form.get("zip_col"))
        )

        main_col = int(request.form.get("main_col"))
        zip_col = int(request.form.get("zip_col"))
        other_cols = parse_input(request.form.get("sec_col"))
        other_cols.insert(0, main_col)

        col_names = input_df.columns.tolist()
        max_value = input_df[col_names[main_col]].max()
        min_value = input_df[col_names[main_col]].min()

        input_df[col_names[zip_col]] = input_df[col_names[zip_col]].astype(str)

        input_df["Parsed Zip Code"] = (
            input_df[col_names[zip_col]].str.extract(r"(\d{5})")[0].astype(str)
        )

        us_zips = pd.read_csv("./static/USA_zip_list.csv", dtype={"zip_code": str})

        us_zips["zip_code"] = us_zips["zip_code"].astype(str)

        merged_data = pd.merge(
            input_df,
            us_zips,
            how="left",
            left_on="Parsed Zip Code",
            right_on="zip_code",
        )

        unique_codes = merged_data["state_code"].unique().tolist()
        state_values = []

        for code in unique_codes:
            state_name = STATE_DICT.get(code)
            state_values.append(state_name)

        filtered_new_geo_df = None

        for state_code, state_name in zip(unique_codes, state_values):
            file_name = f"./State-zip-code-GeoJSON/{state_code.lower()}_{state_name}_zip_codes_geo.min.json"

            entire_gdf = gpd.read_file(file_name)

            if filtered_new_geo_df is None:
                filtered_new_geo_df = entire_gdf
            else:
                filtered_new_geo_df = pd.concat([entire_gdf, filtered_new_geo_df])

        file_prefix = request.form.get("city")

        filtered_new_geo_df = filtered_new_geo_df[
            filtered_new_geo_df["ZCTA5CE10"].isin(input_df["Parsed Zip Code"].tolist())
        ]

        merged_geo_json = json.loads(
            pd.merge(
                filtered_new_geo_df,
                input_df,
                left_on="ZCTA5CE10",
                right_on="Parsed Zip Code",
                how="left",
            )
            .fillna(0)
            .to_json()
        )

        bounds = filtered_new_geo_df.total_bounds
        centroid = filtered_new_geo_df.geometry.unary_union.centroid
        latitude = centroid.y
        longitude = centroid.x

        coordinates = [latitude, longitude]
        m = folium.Map(location=coordinates)
        m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

        Choropleth(
            geo_data=merged_geo_json,
            name="choropleth",
            data=input_df,
            columns=[
                "Parsed Zip Code",
                col_names[main_col],
            ],
            key_on="feature.properties.ZCTA5CE10",
            fill_color="YlOrRd",
            fill_opacity=0.6,
            line_opacity=0.2,
            legend_name=col_names[main_col],
            highlight=True,
        ).add_to(m)

        style_function = lambda x: {
            "fillColor": color_scale(
                x["properties"][col_names[main_col]], max_value, min_value
            ),
            "color": "black",
            "weight": 1,
            "fillOpacity": 0.7,
        }
        highlight_function = lambda x: {
            "fillColor": "#000000",
            "color": "#000000",
            "fillOpacity": 0.50,
            "weight": 0.1,
        }
        fields = ["ZCTA5CE10"]
        aliases = ["Zip Code: "]

        for i, name in enumerate(col_names):
            if i in other_cols:
                fields.append(name)
                aliases.append(name + ": ")

        NIL = folium.features.GeoJson(
            merged_geo_json,
            style_function=style_function,
            control=False,
            highlight_function=highlight_function,
            tooltip=folium.features.GeoJsonTooltip(
                fields=fields,
                aliases=aliases,
                style=(
                    "background-color: white; color: #333333; font-family: arial; font-size: 12px; padding: 10px;"
                ),
            ),
        )
        m.add_child(NIL)
        m.keep_in_front(NIL)

        # Get column names from sec_col input before generating the KML
        sec_col_input = request.form.get("sec_col", "")
        if sec_col_input:  # Only if sec_col_input is provided
            kml_cols = get_column_names_from_indices(input_df, sec_col_input)
        else:
            kml_cols = []

        # Then, pass other_cols along with the data to the KML generation function
        kml_file_path = generate_kml(
            merged_geo_json,
            col_names[main_col],
            kml_cols,
            max_value,
            min_value,
            file_prefix,
        )

        kml_filename = os.path.basename(
            kml_file_path
        )  # Get the file name from the path

        # Save the generated heatmap
        unique_filename = f"{uuid.uuid4().hex}.html"
        save_path = os.path.join(HEATMAP_DIR, file_prefix + "_" + unique_filename)

        m.save(save_path)

        # Delete the user input spreadsheet
        os.remove(filepath)

        # Return the link to the user
        return jsonify(
            {
                "status": "success",
                "heatmap_url": f"{os.getenv('base_url_flask')}/heatmap/result/{file_prefix}_{unique_filename}",
                "kml_url": f"{os.getenv('base_url_flask')}/heatmap/result/{kml_filename}",
            }
        )
    else:
        return jsonify({"error": "Invalid file type"}), 400


@app.route("/joke/beau", methods=["POST"])
def makeJoke():
    theme = request.json.get("theme")
    responses = request.json.get("responses")
    if not theme:
        theme = "anything"
    if not responses:
        responses = {
            "role": "system",
            "content": "There are no previous messages in the chat log, do not worry about creating duplicate jokes.",
        }
    response = ai_client.chat.completions.create(
        model="gpt-4-1106-preview",
        temperature=1,
        messages=[
            {
                "role": "system",
                "content": """You are the President of the company LaneTerralever, 
                    or more commonly known as just 'LT'. You are about 60 years old 
                    and have 40 years of experience in marketing and business. You are 
                    also known for being funny but in a dad-joke way with lots of puns 
                    and plays on words. When you tell a joke, the usual response is 
                    'Wow' followed by a couple chuckles. People refer to you as Beau 
                    Lane but sometimes spell it wrong like 'Bo'. Users will come to you 
                    to give them a joke and it is your job to create a concise and funny 
                    dad joke. The maximum length of the joke should be 30 words, but 
                    on average, the jokes should be around 10-20 words. Do not use a joke 
                    that has already been used in the current chat logs. There is no need 
                    to state that you understand the request and you should only respond 
                    with the joke and nothing else.""",
            },
            responses,
            {"role": "user", "content": f"Please give me a dad joke about: {theme}"},
        ],
    )
    moderation = ai_client.moderations.create(input=str(response.choices[0].message))
    if moderation.results[0].flagged:
        return jsonify({"error": "joke was deemed inappropriate"}), 304
    return str(response.choices[0].message.content)


if __name__ == "__main__":
    app.run()
