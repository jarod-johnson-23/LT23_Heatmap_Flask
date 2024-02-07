from flask import Blueprint, request, jsonify, send_file
import pandas as pd
import os
from werkzeug.utils import secure_filename
import io
from openpyxl.styles import NamedStyle

toyota_bp = Blueprint("toyota_bp", __name__)


from . import routes

TOYOTA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "toyota/files")


# Toyota Media Buy Processing Project
# Step 1: Modify Media Name and Budget Code
def modify_media_and_budget(toyota_data, YC):
    toyota_data["Media Name"] = toyota_data["Media Name"].replace(
        {"Cable": "Broadcast", "TV": "Broadcast", "Radio": "Radio/Online Radio"}
    )

    toyota_data["Budget Code"] = toyota_data["Budget Code"].replace(
        {"Event": f"TDA{YC}", "TCUV": f"TCUV{YC}", "Parts and Service": f"PS{YC}"}
    )

    return toyota_data


# Toyota Media Buy Processing Project
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


# Toyota Media Buy Processing Project
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


# Toyota Media Buy Processing Project
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


# Toyota Media Buy Processing Project
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


# Toyota Media Buy Processing Project
# Load the data
def load_data():
    nielsen_data = pd.read_excel(os.path.join(TOYOTA_DIR, "NielsenCalendar.xlsx"))
    return nielsen_data


# Toyota Media Buy Processing Project
@toyota_bp.route("/media_buy_processing", methods=["POST"])
def toyota_media_buy_processing():
    try:
        YC = request.form.get("YC")
        MC = request.form.get("MC")

        toyota_file = request.files.get("toyota_file")
        coop_file = request.files.get("coop_file")

        if toyota_file and coop_file and YC and MC:
            toyota_filename = secure_filename(toyota_file.filename)
            coop_filename = secure_filename(coop_file.filename)

            toyota_filepath = os.path.join(TOYOTA_DIR, toyota_filename)
            coop_filepath = os.path.join(TOYOTA_DIR, coop_filename)

            toyota_file.save(toyota_filepath)
            coop_file.save(coop_filepath)

            nielsen_data = load_data()
            toyota_data = pd.read_excel(toyota_filepath)
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
