import os
import io
import uuid
from flask import Flask, request, jsonify, send_from_directory, send_file
import pandas as pd
import geopandas as gpd
import folium
from folium import Choropleth
import json
from werkzeug.utils import secure_filename
from datetime import timedelta
from openpyxl import load_workbook
from openpyxl.styles import NamedStyle
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

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

        # # If it's a whole number, return as integer with commas
        # if number.is_integer():
        #     return "{:,.0f}".format(number)
        # # If it has decimals, return with commas and retain original decimal places
        # else:
        #     # Convert the number to string and count the number of decimal places
        #     decimal_places = len(str(number).split(".")[1])
        #     format_string = "{:,.%df}" % decimal_places
        #     return format_string.format(number)

    except ValueError:
        return "0"


# Load the data
def load_data():
    toyota_data = pd.read_excel("Toyota E Submission V2- June 2023.xlsx", skiprows=1)
    coop_data = pd.read_excel("2023 Co-op model rotation.xlsx").drop(
        columns=["Total"], errors="ignore"
    )
    nielsen_data = pd.read_excel("Nielsen Calendar.xlsx")
    return toyota_data, coop_data, nielsen_data


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
        if row["Vehicle Series Name"] == "Brand":
            toyota_data.at[index, "Activity Start Date"] = (
                nielsen_calendar["Start Date"].dt.strftime("%m-%d-%Y").values[0]
            )
            toyota_data.at[index, "Activity End Date"] = (
                nielsen_calendar["End Date"].dt.strftime("%m-%d-%Y").values[0]
            )

    toyota_data = toyota_data.append(new_rows, ignore_index=True)
    return toyota_data


# Save to Excel with currency formatting
def save_to_excel(toyota_data, output):
    # Step 1: Save the DataFrame to an Excel file stored in a BytesIO object
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        toyota_data.to_excel(writer, index=False)

    # Step 2: Load the workbook from the BytesIO object
    output.seek(0)
    wb = load_workbook(output)
    ws = wb.active

    # Step 3: Apply currency formatting
    currency_style = NamedStyle(name="currency", number_format="$#,##0.00")
    for row in ws.iter_rows(
        min_row=2, max_row=ws.max_row, min_col=7, max_col=9
    ):  # Columns G to I
        for cell in row:
            cell.style = currency_style

    # Step 4: Save the workbook back to the BytesIO object
    output.seek(0)
    wb.save(output)
    output.seek(0)


@app.route("/")
def index():
    return {"STATUS": "OK", "CODE": 200}


@app.route("/toyota_media_buy_processing")
def toyota_media_buy_processing():
    YC = request.form.get("YC")
    MC = request.form.get("MC")
    toyota_data, coop_data, nielsen_data = load_data()
    toyota_data = modify_media_and_budget(toyota_data, YC)
    toyota_data = update_campaign(toyota_data, YC, MC)
    toyota_data = update_diversity(toyota_data)
    toyota_data = modify_dates_and_amounts(toyota_data, coop_data, nielsen_data, YC, MC)
    # Use BytesIO instead of saving to disk
    output = io.BytesIO()
    save_to_excel(toyota_data, output)

    # Set the file pointer to the beginning
    output.seek(0)

    # Create a filename including the current month and year
    from datetime import datetime

    current_month = datetime.now().strftime("%B")
    current_year = datetime.now().year
    filename = f"Toyota E Submission - Final - {current_month}-{current_year}.xlsx"

    # Return the Excel file
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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
                "heatmap_url": f"https://py.laneterraleverapi.org/heatmap/result/{file_prefix}_{unique_filename}",
            }
        )
    else:
        return jsonify({"error": "Invalid file type"}), 400


if __name__ == "__main__":
    app.run()
