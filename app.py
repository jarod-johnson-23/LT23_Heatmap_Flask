import os
import uuid
from flask import Flask, request, jsonify, send_from_directory
import pandas as pd
import geopandas as gpd
import folium
from folium import Choropleth
import json
from werkzeug.utils import secure_filename
from waitress import serve

app = Flask(__name__)

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


@app.route("/")
def index():
    return {"STATUS": "OK", "CODE": 200}


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

        if filename.rsplit(".", 1)[1].lower() == "csv":
            input_df = pd.read_csv(excel_path)
        else:
            input_df = pd.read_excel(excel_path)

        main_col = 1
        zip_col = 0
        other_cols = []

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

        for name in col_names[1:]:
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