import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Counter Test", layout="centered")

# 1. Authenticate using Streamlit Secrets
@st.cache_resource
def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    client = gspread.authorize(creds)
    # Open the first worksheet by Sheet ID
    return client.open_by_key(st.secrets["sheet_id"]).sheet1

sheet = get_sheet()

# 2. Read current counts from Row 2 (A2 and B2)
def read_counts():
    row = sheet.row_values(2)
    count_a = int(row[0]) if len(row) > 0 and row[0].isdigit() else 0
    count_b = int(row[1]) if len(row) > 1 and row[1].isdigit() else 0
    return count_a, count_b

count_a, count_b = read_counts()

st.title("A / B Button Counter")

col1, col2 = st.columns(2)

with col1:
    st.metric(label="Count A", value=count_a)
    if st.button("A", use_container_width=True):
        sheet.update_acell("A2", count_a + 1)
        st.rerun()

with col2:
    st.metric(label="Count B", value=count_b)
    if st.button("B", use_container_width=True):
        sheet.update_acell("B2", count_b + 1)
        st.rerun()
