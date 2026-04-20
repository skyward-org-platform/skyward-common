from typing import List, Tuple, Dict, Optional, Any
import pandas as pd
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
try:
    from IPython.display import HTML, FileLink, display
except ImportError:
    HTML = FileLink = display = None
import tldextract



# Display a scrollable HTML-rendered DataFrame with wrapped text cells
def display_scrollable_df(df, height_px="400px"):
    """
    Displays a pandas DataFrame as a scrollable, wrapped HTML table in Jupyter environments.

    Args:
        df (pd.DataFrame): The DataFrame to display.
    """
    styles = """
    <style>
        td {
            white-space: normal !important;
            word-wrap: break-word !important;
            max-width: 300px;
        }
    </style>
    """
    html = df.to_html(escape=False)
    html_output = f'<div style="height:{height_px}; overflow:auto;">{html}</div>'
    display(HTML(styles + html_output))

# Return the current date formatted as 'YYYY-MM-DD' in New York timezone
def get_formatted_date():
    """
    Returns the current date as a string in 'YYYY-MM-DD' format based on New York time.

    Returns:
        str: The formatted date string.
    """
    return datetime.now(ZoneInfo("America/New_York")).strftime('%Y-%m-%d')


# Execute a Google API request safely with automatic retries on failure
def safe_execute(req, retries=3, delay=5):
    """
    Executes a Google API request with retry logic to handle transient errors (e.g., network timeouts,
    5xx responses, or rate limits). If the request fails, it retries up to the specified number of
    attempts with a delay between each try.

    Args:
        req: The API request object with an `.execute()` method (e.g., from the Google API client).
        retries (int, optional): Maximum number of retry attempts before raising an error. Defaults to 3.
        delay (int or float, optional): Delay in seconds between retry attempts. Defaults to 5.

    Returns:
        dict: The API response if successful.

    Raises:
        RuntimeError: If all retry attempts fail.
    """
    for attempt in range(retries):
        try:
            return req.execute()
        except Exception as e:
            print(f"Request failed (attempt {attempt + 1}): {e}")
            time.sleep(delay)
    raise RuntimeError("All execution attempts failed.")


# Upload a DataFrame to a new or existing Google Sheet, with optional folder placement and formatting
def upload_df_to_google_sheets(df, folder_id, spreadsheet_name, sheet_name, drive_service, sheets_service, spreadsheet_id=None):
    """
    Uploads a pandas DataFrame to a Google Sheet. If no spreadsheet ID is provided, creates a new
    spreadsheet, moves it into the given Drive folder, and shares it publicly (view-only). Otherwise,
    adds a new sheet to the existing spreadsheet.

    Args:
        df (pd.DataFrame): The DataFrame to upload.
        spreadsheet_name (str): Name for the new spreadsheet (used if spreadsheet_id is None).
        sheet_name (str): Name of the sheet tab where the data will be inserted.
        folder_id (str): Google Drive folder ID where the spreadsheet will be stored.
        spreadsheet_id (str or None): If provided, the existing spreadsheet to update.

    Returns:
        str: The spreadsheet ID of the created or updated Google Sheet.
    """

    today = get_formatted_date()

    new_doc = True if spreadsheet_id is None else False

    try:
        # If no spreadsheet_id is given, create a new one
        if spreadsheet_id is None:
            file_meta = {
                "name": f"{spreadsheet_name}_{datetime.today().strftime('%Y-%m-%d')}",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "parents": [folder_id],  # <-- shared drive folder id
            }
            sheet_file = drive_service.files().create(
                body=file_meta,
                fields="id,name,webViewLink,driveId,parents",
                supportsAllDrives=True,       # REQUIRED for shared drives
            ).execute()

            spreadsheet_id = sheet_file["id"]
            print(f"Created spreadsheet KGA Shared Drive: {sheet_file['webViewLink']}")

            # Optional: rename the default sheet if needed
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{
                    "updateSheetProperties": {
                        "properties": {"sheetId": 0, "title": sheet_name},
                        "fields": "title",
                    }
                }]}
            ).execute()

            # Optional: make it viewable by anyone
            drive_service.permissions().create(
                fileId=spreadsheet_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
                supportsAllDrives=True,
                sendNotificationEmail=False,
            ).execute()

        else:
            # Add a new sheet to the existing spreadsheet
            safe_execute(sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "requests": [{
                        "addSheet": {
                            "properties": {"title": sheet_name}
                        }
                    }]
                }
            ))


        # Fetch metadata to get sheetId from sheet title
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

        sheet_id = None
        for sheet in sheet_metadata['sheets']:
            if sheet['properties']['title'] == sheet_name:
                sheet_id = sheet['properties']['sheetId']
                break

        if sheet_id is None:
            raise ValueError(f"Sheet '{sheet_name}' not found in spreadsheet.")

        # Resize sheet to fit the DataFrame
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [{
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {
                                "rowCount": len(df) + 1,  # +1 for header
                                "columnCount": df.shape[1]
                            }
                        },
                        "fields": "gridProperties(rowCount,columnCount)"
                    }
                }]
            }
        ).execute()

        # Resize the sheet to fit the DataFrame (including header)
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [{
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {
                                "rowCount": len(df) + 1,  # +1 for header
                                "columnCount": df.shape[1]
                            }
                        },
                        "fields": "gridProperties(rowCount,columnCount)"
                    }
                }]
            }
        ).execute()

        # Prepare DataFrame for upload
        df_t = df.copy().astype(object)
        for col in df_t.columns:
            if col.endswith('_url'):
                df_t[col] = df_t[col].where(pd.notnull(df_t[col]), 'Not ranking')
            else:
                df_t[col] = df_t[col].where(pd.notnull(df_t[col]), '')

        values = [df_t.columns.tolist()] + df_t.values.tolist()
        body = {'values': values}

        # Upload data
        MAX_CELLS_PER_BATCH = 500_000
        num_cols = len(values[0])
        rows_per_batch = MAX_CELLS_PER_BATCH // num_cols

        num_rows = len(values)
        num_batches = (num_rows // rows_per_batch) + 1
        # Upload in batches
        for i in range(num_batches):
            start = i * rows_per_batch
            end = min(start + rows_per_batch, num_rows)
            batch = values[start:end]
            if not batch:
                continue

            start_row = start + 1  # Google Sheets is 1-indexed
            range_ref = f"{sheet_name}!A{start_row}"

            tries = 3
            while tries > 0:
                try:
                    sheets_service.spreadsheets().values().update(
                        spreadsheetId=spreadsheet_id,
                        range=range_ref,
                        valueInputOption="RAW",
                        body={'values': batch}
                    ).execute()

                    tries = 0
                except Exception as e:
                    tries -= 1
                    if tries == 0:
                        raise e
                    else:
                        print(f"Batch upload failed, trying {tries} more times. {e}")
                        time.sleep(5)

    except Exception as e:
        if new_doc:
            # delete doc
            drive_service.files().delete(fileId=spreadsheet_id).execute()
        raise e

    return spreadsheet_id


# Extract the root domain from a full URL (e.g., "example.com")
def get_domain(url):
    """
    Extracts the domain name from a full URL using tldextract.

    Parameters:
        url (str): The full website URL (e.g., https://example.com/path).

    Returns:
        str: The domain in the format 'example.com'.
    """
    # Extract domain components from the URL
    ext = tldextract.extract(url)

    # Reconstruct domain name from extracted parts
    return f"{ext.domain}.{ext.suffix}"



# Extract the path from a full URL (e.g., "/path/to/page")
def get_path(url):
    """
    Extracts the path component from a full URL (everything after the domain).

    Parameters:
        url (str): The full website URL (e.g., https://example.com/path/to/page?query=1).

    Returns:
        str: The path portion of the URL (e.g., "/path/to/page").
              Returns "/" if no explicit path exists.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return parsed.path or "/"



# Prompts user for a valid date input and returns a datetime.date object
def prompt_for_date(prompt="Enter a date"):
    while True:
        try:
            print(prompt)
            year = int(input("  Year (YYYY): "))
            month = int(input("  Month (1-12): "))
            day = int(input("  Day (1-31): "))
            return date(year, month, day)  # date(...) will raise if invalid
        except ValueError as exc:
            print(f"Invalid input or date ({exc}). Please try again.")


# Prompt user to select a period length from predefined options or enter a custom value
def prompt_period_length():
    options = {
        "1": ("1 week", 7),
        "2": ("30 days", 30),
        "3": ("90 days", 90),
        "4": ("180 days (6 months)", 180),
        "5": ("365 days (1 year)", 365),
        "6": ("Custom (X days)", None),
    }

    while True:
        print("Select period length:")
        for key, (label, days) in options.items():
            suffix = f" = {days} days" if days else ""
            print(f"  {key}. {label}{suffix}")

        choice = input("Choice [1-6]: ").strip()
        if choice not in options:
            print("Invalid choice. Try again.")
            continue

        label, days = options[choice]
        if days is not None:
            return days

        # Custom
        try:
            custom = int(input("Enter number of days: ").strip())
            if custom <= 0:
                raise ValueError("Must be positive")
            return custom
        except ValueError as exc:
            print(f"Invalid number ({exc}). Try again.")



# ---------------------------------------------------------------------------
# UUID helpers for BQ job/upload tracking
# ---------------------------------------------------------------------------

import uuid as _uuid


def generate_job_id() -> str:
    """Return a new UUID4 string suitable for use as a `job_id`."""
    return str(_uuid.uuid4())



def generate_upload_id() -> str:
    """Return a new UUID4 string suitable for use as an `upload_id`."""
    return str(_uuid.uuid4())
