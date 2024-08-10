from flask import Blueprint, current_app, request, jsonify, send_from_directory
import os
import json
import folium
import uuid
import simplekml
import pandas as pd
import geopandas as gpd
from folium import Choropleth
from werkzeug.utils import secure_filename

heatmap_bp = Blueprint("heatmap_bp", __name__)

from . import routes

# Zipcode Heatmap Generation Project
# Directory to save the generated heatmaps
HEATMAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heatmap/result")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heatmap/data")
ZIPS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static/USA_zip_list.csv"
)
GEO_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "State-zip-code-GeoJSON"
)

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


# Zipcode Heatmap Generation Project
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# Zipcode Heatmap Generation Project
def parse_input(input_str):
    if input_str:
        return list(map(int, input_str.split(",")))
    else:
        return []


# Zipcode Heatmap Generation Project
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


# Zipcode Heatmap Generation Project
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


# Zipcode Heatmap Generation Project
@heatmap_bp.route("/result/<filename>")
def serve_file(filename):
    # Modify the function to serve both HTML and KML files with content type checks
    if ".." in filename or filename.startswith("/"):
        return "Invalid path!", 400

    file_path = os.path.join(HEATMAP_DIR, filename)
    print(file_path)
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


# Zipcode Heatmap Generation Project
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


# Zipcode Heatmap Generation Project
def create_kml_polygon(
    kml, polygon, feature, col_name, other_cols, max_value, min_value
):
    poly = kml.newpolygon(name=feature["properties"]["ZCTA5CE10"])

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


# Zipcode Heatmap Generation Project
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


# Zipcode Heatmap Generation Project
def html_color_to_kml_color(html_color, alpha="22"):
    # Assume html_color is of the form "#rrggbb".
    # strip the leading '#' and add alpha opacity value which is at the beginning in KML color codes
    kml_color = alpha + html_color[5:7] + html_color[3:5] + html_color[1:3]
    return kml_color


# Zipcode Heatmap Generation Project
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


# Zipcode Heatmap Generation Project
def get_column_names_from_indices(df, sec_col_input):
    # Split the input string into a list of indices
    indices = [int(x.strip()) for x in sec_col_input.split(",")]

    # Convert indices to column names, assuming 1-based indices from user input
    column_names = [df.columns[index] for index in indices]

    return column_names


@heatmap_bp.route("/zipcode", methods=["POST"])
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
        filepath = os.path.join(DATA_DIR, filename)
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

        # Ensure zip codes are treated as strings
        input_df[col_names[zip_col]] = input_df[col_names[zip_col]].astype(str).str.zfill(5)

        # Extract the zip codes while ensuring leading zeros are kept
        input_df["Parsed Zip Code"] = input_df[col_names[zip_col]].str.extract(r"(\d{5})")[0].astype(str).str.zfill(5)

        us_zips = pd.read_csv(ZIPS_DIR, dtype={"zip_code": str})

        us_zips["zip_code"] = us_zips["zip_code"].astype(str).str.zfill(5)

        merged_data = pd.merge(
            input_df,
            us_zips,
            how="left",
            left_on="Parsed Zip Code",
            right_on="zip_code",
        )

        # Filter out rows where the zip code was not found in us_zips
        merged_data = merged_data[merged_data["zip_code"].notnull()]

        unique_codes = merged_data["state_code"].unique().tolist()
        state_values = []

        for code in unique_codes:
            state_name = STATE_DICT.get(code)
            state_values.append(state_name)

        filtered_new_geo_df = None

        for state_code, state_name in zip(unique_codes, state_values):
            file_name = (
                f"{GEO_DIR}/{state_code.lower()}_{state_name}_zip_codes_geo.min.json"
            )

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
