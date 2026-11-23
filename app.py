import io
import re
import base64
import streamlit as st
import streamlit.components.v1 as components
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(page_title="EDFN Morphology Classifier", layout="wide")

RADIO_COLS = ["Irregular","1-sided jet","HT", "NAT",  "FR II", "FRI","WAT", "HyMoRS", "Unclassified"]
HOST_COLS = ["Elliptical", "Spiral", "Merger/like"]
ALL_CLASS_COLS = RADIO_COLS + HOST_COLS

# ---------------------------------------------------------
# 1. Authenticate Google Sheets & Drive APIs
# ---------------------------------------------------------
@st.cache_resource
def init_google_clients():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    gc = gspread.authorize(creds)
    worksheet = gc.open_by_key(st.secrets["sheet_id"]).worksheet("Classification")
    drive_service = build("drive", "v3", credentials=creds)
    return worksheet, drive_service

sheet, drive = init_google_clients()

# ---------------------------------------------------------
# 2. Index PNG Files in Google Drive Folder
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def index_drive_pngs(folder_id: str):
    """Fetch all PNG filenames and file IDs from the Drive folder."""
    files_by_name = {}
    parsed_coords = []
    page_token = None
    pattern = re.compile(r"RA([+-]?\d+\.?\d*)_DEC([+-]?\d+\.?\d*)_RGBC\.png$", re.IGNORECASE)

    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false and mimeType = 'image/png'",
            fields="nextPageToken, files(id, name)",
            pageSize=1000,
            pageToken=page_token,
        ).execute()

        for f in resp.get("files", []):
            name = f["name"]
            fid = f["id"]
            files_by_name[name] = fid
            m = pattern.search(name)
            if m:
                parsed_coords.append((float(m.group(1)), float(m.group(2)), fid, name))

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return files_by_name, parsed_coords

@st.cache_data(ttl=3600)
def download_png_bytes(file_id: str) -> bytes:
    """Download PNG image bytes into memory for instant display."""
    request = drive.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()

def render_hover_loupe(img_bytes: bytes, caption: str, width: int = 500, zoom: float = 2.5, lens_size: int = 160):
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    html = f"""
    <div style="text-align:center; font-family: sans-serif;">
      <div id="img-container" style="position:relative; display:inline-block; cursor:crosshair;">
        <img id="target-img" src="data:image/png;base64,{b64}" style="width:{width}px; height:auto; display:block; border-radius:4px;" />
        <div id="loupe" style="
            display:none; position:absolute; pointer-events:none;
            width:{lens_size}px; height:{lens_size}px; border-radius:50%;
            border:2px solid #ff4b4b; box-shadow:0 0 10px rgba(0,0,0,0.6);
            background-repeat:no-repeat; z-index:99;">
        </div>
      </div>
      <div style="color:#888; font-size:13px; margin-top:6px;">{caption} (Hover to magnify {zoom}x)</div>
    </div>
    <script>
      const img = document.getElementById("target-img");
      const loupe = document.getElementById("loupe");
      const zoom = {zoom};
      const half = {lens_size} / 2;

      img.addEventListener("mousemove", (e) => {{
        const rect = img.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        loupe.style.display = "block";
        loupe.style.left = (x - half) + "px";
        loupe.style.top = (y - half) + "px";
        loupe.style.backgroundImage = `url('${{img.src}}')`;
        loupe.style.backgroundSize = `${{rect.width * zoom}}px ${{rect.height * zoom}}px`;
        loupe.style.backgroundPosition = `-${{x * zoom - half}}px -${{y * zoom - half}}px`;
      }});

      img.addEventListener("mouseleave", () => {{
        loupe.style.display = "none";
      }});
    </script>
    """
    components.html(html, height=width + 65)

def find_image_for_coord(ra_val, dec_val, files_by_name, parsed_coords):
    """Match RA_JD and Dec_JD to RA{ra}_DEC{dec}_RGBC.png."""
    try:
        ra_f = float(ra_val)
        dec_f = float(dec_val)
    except (ValueError, TypeError):
        return None, None

    # 1. Try exact string match (4 decimal places with explicit + on Dec)
    expected_name = f"RA{ra_f:.4f}_DEC{dec_f:+.4f}_RGBC.png"
    if expected_name in files_by_name:
        return files_by_name[expected_name], expected_name

    # 2. Fallback: raw string match if Sheet already has exact string formatting
    raw_name = f"RA{str(ra_val).strip()}_DEC{str(dec_val).strip()}_RGBC.png"
    if raw_name in files_by_name:
        return files_by_name[raw_name], raw_name

    # 3. Fallback: nearest coordinate within 0.0002 deg (~0.7 arcsec)
    for f_ra, f_dec, fid, fname in parsed_coords:
        if abs(f_ra - ra_f) < 2e-4 and abs(f_dec - dec_f) < 2e-4:
            return fid, fname

    return None, expected_name

# ---------------------------------------------------------
# 3. Load Sheet Data into Session State
# ---------------------------------------------------------
def load_sheet_data():
    all_values = sheet.get_all_values()
    headers = [h.strip() for h in all_values[0]]
    rows = []
    for idx, r in enumerate(all_values[1:], start=2):
        # Pad row if trailing cells are blank
        padded = r + [""] * (len(headers) - len(r))
        row_dict = dict(zip(headers, padded))
        row_dict["_sheet_row"] = idx
        rows.append(row_dict)
    return headers, rows

if "rows" not in st.session_state:
    st.session_state.headers, st.session_state.rows = load_sheet_data()
    # Jump to first unclassified source automatically on startup
    first_unclassified = 0
    for i, r in enumerate(st.session_state.rows):
        radio_sum = sum(1 for c in RADIO_COLS if str(r.get(c, "")).strip() == "1")
        if radio_sum == 0:
            first_unclassified = i
            break
    st.session_state.idx = first_unclassified

headers = st.session_state.headers
rows = st.session_state.rows
total = len(rows)

if total == 0:
    st.warning("No target rows found in the 'Classification' sheet.")
    st.stop()

files_by_name, parsed_coords = index_drive_pngs(st.secrets["drive_folder_id"])

# ---------------------------------------------------------
# 4. Update Function (Single Batch API Call per Click)
# ---------------------------------------------------------
def update_classification(row_idx_in_list: int, category_type: str, selected_label: str, auto_advance: bool = True):
    row_data = st.session_state.rows[row_idx_in_list]
    sheet_row_num = row_data["_sheet_row"]

    if category_type == "radio":
        for col in RADIO_COLS:
            row_data[col] = "1" if col == selected_label else "0"
    elif category_type == "host":
        for col in HOST_COLS:
            row_data[col] = "1" if col == selected_label else "0"

    # Build contiguous row slice for E:N (all 10 classification columns)
    start_col_idx = headers.index(RADIO_COLS[0]) + 1
    end_col_idx = headers.index(HOST_COLS[-1]) + 1
    start_a1 = gspread.utils.rowcol_to_a1(sheet_row_num, start_col_idx)
    end_a1 = gspread.utils.rowcol_to_a1(sheet_row_num, end_col_idx)

    values_to_write = [[int(row_data.get(col) or 0) for col in ALL_CLASS_COLS]]
    sheet.update(range_name=f"{start_a1}:{end_a1}", values=values_to_write)

    if auto_advance and row_idx_in_list < total - 1:
        st.session_state.idx += 1

# ---------------------------------------------------------
# 5. User Interface & Navigation
# ---------------------------------------------------------
curr_idx = st.session_state.idx
curr_row = rows[curr_idx]

# Top Sidebar / Header Controls
with st.sidebar:
    st.header("Navigation & Progress")
    classified_count = sum(
        1 for r in rows if any(str(r.get(c, "")).strip() == "1" for c in RADIO_COLS)
    )
    st.progress(classified_count / total)
    st.write(f"**Classified:** {classified_count} / {total}")

    jump_num = st.number_input("Jump to Source #", min_value=1, max_value=total, value=curr_idx + 1)
    if jump_num - 1 != curr_idx:
        st.session_state.idx = jump_num - 1
        st.rerun()

    auto_next = st.checkbox("Auto-advance after Radio click", value=True)
    if st.button("Reload Sheet & Drive Cache"):
        st.cache_data.clear()
        del st.session_state["rows"]
        st.rerun()

# Prev / Next Bar
nav1, nav2, nav3 = st.columns([1, 3, 1])
with nav1:
    if st.button("⬅️ Previous", use_container_width=True, disabled=(curr_idx == 0)):
        st.session_state.idx -= 1
        st.rerun()
with nav2:
    ra_f = curr_row.get("RA_final", "")
    dec_f = curr_row.get("DEC_final", "")
    # Format to 4 decimals if numeric, otherwise show raw/blank
    try:
        host_str = f"{float(ra_f):.4f} {float(dec_f):.4f}"
    except (ValueError, TypeError):
        host_str = f"{ra_f} {dec_f}".strip() or "N/A"

st.markdown(
        f"<h4 style='text-align:center;'>Source {curr_idx + 1} of {total} &nbsp;|&nbsp; "
        f"RA_JD: <code>{curr_row.get('Ra_JD')}</code> &nbsp; Dec_JD: <code>{curr_row.get('Dec_JD')}</code> &nbsp;|&nbsp; "
        f"Optical host : <code>{host_str}</code></h4>",
        unsafe_allow_html=True,
    )
with nav3:
    if st.button("Next ➡️", use_container_width=True, disabled=(curr_idx >= total - 1)):
        st.session_state.idx += 1
        st.rerun()

st.divider()

# Main Layout: Image on Left, Buttons on Right
img_col, ctrl_col = st.columns([1.2, 1])

with img_col:
    file_id, matched_name = find_image_for_coord(
        curr_row.get("Ra_JD"), curr_row.get("Dec_JD"), files_by_name, parsed_coords
    )
    if file_id:
        img_bytes = download_png_bytes(file_id)
        render_hover_loupe(img_bytes, matched_name, width=500, zoom=3.5, lens_size=180)
    else:
        st.error(f"Image not found in Drive folder for expected filename: `{matched_name}`")


with ctrl_col:
    # --- Section A: Radio Morphology ---
    st.subheader("a. Radio Morphology")
    active_radio = [c for c in RADIO_COLS if str(curr_row.get(c, "")).strip() == "1"]
    st.write(f"Current: **{', '.join(active_radio) if active_radio else 'None'}**")

    r_cols = st.columns(2)
    for i, label in enumerate(RADIO_COLS):
        is_selected = label in active_radio
        btn_type = "primary" if is_selected else "secondary"
        if r_cols[i % 2].button(label, key=f"radio_{curr_idx}_{label}", type=btn_type, use_container_width=True):
            update_classification(curr_idx, "radio", label, auto_advance=auto_next)
            st.rerun()

    st.divider()

    # --- Section B: Host Morphology ---
    st.subheader("b. Host Morphology")
    active_host = [c for c in HOST_COLS if str(curr_row.get(c, "")).strip() == "1"]
    st.write(f"Current: **{', '.join(active_host) if active_host else 'None'}**")

    h_cols = st.columns(3)
    for i, label in enumerate(HOST_COLS):
        is_selected = label in active_host
        btn_type = "primary" if is_selected else "secondary"
        if h_cols[i].button(label, key=f"host_{curr_idx}_{label}", type=btn_type, use_container_width=True):
            # Do not auto-advance on Host click if Radio hasn't been set yet
            update_classification(curr_idx, "host", label, auto_advance=False)
            st.rerun()
