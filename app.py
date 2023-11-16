import os
import io
import uuid
import base64
from flask import Flask, request, jsonify, send_from_directory, send_file, make_response
import pandas as pd
import geopandas as gpd
import folium
from folium import Choropleth
import json
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from openpyxl.styles import NamedStyle
from pymongo import MongoClient
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity,
)
from itsdangerous import SignatureExpired, BadSignature, URLSafeTimedSerializer
from flask_cors import CORS

app = Flask(__name__)
CORS(
    app,
    resources={r"/*": {"origins": "http://localhost:3000"}},
    supports_credentials=True,
)
bcrypt = Bcrypt(app)

# Configure Flask-PyMongo
mongo_uri = "mongodb+srv://root:7Q8rCm9iFrC2zofi@cluster0.ft9jkhd.mongodb.net/?retryWrites=true&w=majority"
client = MongoClient(mongo_uri)
db = client["LT-db-dashboard"]
user_collection = db["userInfo"]
user_collection.create_index("email", unique=True)

# app.config["MAIL_SERVER"] = "smtp.lt.agency"  # The SMTP server domain
# app.config["MAIL_PORT"] = 465  # Typically, 587 for TLS or 465 for SSL
# app.config["MAIL_USE_TLS"] = False  # Use TLS
# app.config["MAIL_USE_SSL"] = True  # Use SSL (pick TLS or SSL, not both)
# app.config["MAIL_USERNAME"] = "jarod.johnson@lt.agency"
# app.config["MAIL_PASSWORD"] = "JJaug23LT"
# app.config["MAIL_DEFAULT_SENDER"] = "jarod.johnson@lt.agency"
#
# mail = Mail(app)

app.config["TOKEN_KEY"] = "YAPOrj4oXgEe5Fme7kCMLh85"


def generate_jwt_secret_key(length=64):
    # Generate random bytes
    random_bytes = os.urandom(length)
    # Base64 encode the bytes to create a URL-safe secret key
    secret_key = base64.urlsafe_b64encode(random_bytes).decode("utf-8")
    return secret_key


app.config["JWT_SECRET_KEY"] = generate_jwt_secret_key()

# app.config["JWT_SECRET_KEY"] = os.environ.get(
# "JWT_SECRET_KEY", ""
# )  # Change this to a random secret key
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

        token = serializer.dumps(email, salt="LT-Dashboard-Salt")

        # Create a link to the account creation page with the token
        link = f"http://localhost:3000/create-account/{token}"

        # Email content with the link
        email_body = f"Please click on the link to create your account: {link}"

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
    email = request.json.get(
        "email"
    )  # User should submit their email to match the right entry
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


@app.route("/send-email", methods=["POST"])
def send_email():
    email = request.json.get("email")

    # Generate a secure token
    token = serializer.dumps(email, salt="LT-Dashboard-Salt")

    # Create a link to the account creation page with the token
    link = f"http://localhost:3000/create-account/{token}"

    # Email content with the link
    email_body = f"Please click on the link to create your account: {link}"

    return jsonify({"link": link}), 200


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
            salt="LT-Dashboard-Salt",
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


def color_scale(value, max_value, min_value):
    diff = max_value - min_value
    if value is None:
        return "#ffffff00"
    elif value == 0:
        return "#ffffff00"
    elif value < (diff * 0.1) + min_value:
        return "#ffffb2"
    elif value < (diff * 0.25) + min_value:
        return "#feda76"
    elif value < (diff * 0.4) + min_value:
        return "#f5b156"
    elif value < (diff * 0.55) + min_value:
        return "#f59356"
    elif value < (diff * 0.7) + min_value:
        return "#f07f64"
    elif value < (diff * 0.8) + min_value:
        return "#ec5b45"
    elif value < (diff * 0.9) + min_value:
        return "#e73727"
    else:
        return "#d7191c"


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
def serve_heatmap(filename):
    # Check there are no path traversals in the file name
    if ".." in filename or filename.startswith("/"):
        return "Invalid path!", 400

    # Check that it is requesting an HTML file
    if not filename.endswith(".html"):
        return "Invalid file type!", 400

    # Check that the file actually exists
    file_path = os.path.join("./heatmap/result", filename)
    if not os.path.exists(file_path):
        return "File not found!", 404

    return send_from_directory("./heatmap/result", filename)


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
                "heatmap_url": f"http://localhost:5000/heatmap/result/{file_prefix}_{unique_filename}",
            }
        )
    else:
        return jsonify({"error": "Invalid file type"}), 400


if __name__ == "__main__":
    app.run()
