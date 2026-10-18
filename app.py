import streamlit as st
import json
import sqlite3
from datetime import datetime
from PIL import Image
from geopy.distance import geodesic
import folium
from streamlit_folium import st_folium
from google import genai
from google.genai import types
import os

# --- PAGE CONFIG ---
st.set_page_config(page_title="CivicEye AI", page_icon="👁️", layout="wide")

# --- INITIALIZE DB ---
def init_db():
    conn = sqlite3.connect("civic_issues.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            severity TEXT,
            description TEXT,
            lat REAL,
            lon REAL,
            ward TEXT,
            authority_email TEXT,
            upvotes INTEGER DEFAULT 1,
            status TEXT DEFAULT 'Open',
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- MOCK WARD JURISDICTION REGISTRY ---
WARDS = [
    {
        "name": "Ward 80 - Indiranagar",
        "lat": 12.9784, "lon": 77.6408,
        "authority": "BBMP AEE - Solid Waste & Road Infra",
        "email": "aee.indiranagar@bbmp.gov.in"
    },
    {
        "name": "Ward 151 - Koramangala",
        "lat": 12.9352, "lon": 77.6245,
        "authority": "BBMP Health & Infrastructure Inspector",
        "email": "aee.koramangala@bbmp.gov.in"
    },
    {
        "name": "Ward 85 - Whitefield",
        "lat": 12.9698, "lon": 77.7500,
        "authority": "BBMP Executive Engineer & BESCOM Unit",
        "email": "ee.whitefield@bbmp.gov.in"
    }
]

def find_nearest_ward(lat, lon):
    best_ward = WARDS[0]
    min_dist = float("inf")
    for w in WARDS:
        dist = geodesic((lat, lon), (w["lat"], w["lon"])).meters
        if dist < min_dist:
            min_dist = dist
            best_ward = w
    return best_ward

# --- AI MULTIMODAL INFERENCE ---
def analyze_civic_issue(image, api_key):
    client = genai.Client(api_key=api_key)
    prompt = """
    You are an expert civic infrastructure engineer in India.
    Inspect this image and provide output in strict JSON format matching this schema:
    {
        "is_civic_issue": boolean,
        "category": "Pothole" | "Garbage Dump" | "Broken Streetlight" | "Open Manhole" | "Water Leakage" | "Other",
        "severity": "Low" | "Medium" | "High" | "Critical",
        "hazard_details": "string explaining risks to motorists/pedestrians",
        "grievance_subject": "Formal subject line for municipal authority",
        "grievance_body": "Formal, professional grievance letter citing civic hazard and public inconvenience."
    }
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[image, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )
    return json.loads(response.text)

def verify_resolution(before_img, after_img, category, api_key):
    client = genai.Client(api_key=api_key)
    prompt = f"""
    The first image shows a civic issue ({category}). The second image is submitted as proof of repair/cleaning.
    Inspect both images carefully and return strict JSON:
    {{
        "is_resolved": boolean,
        "confidence_score": float (0.0 to 1.0),
        "remarks": "detailed observation of whether the issue was properly fixed or if obstruction remains"
    }}
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[before_img, after_img, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )
    return json.loads(response.text)

# --- SIDEBAR AUTH ---
st.sidebar.title("👁️ CivicEye AI")
api_key = st.sidebar.text_input("Enter Gemini API Key", type="password", value=os.environ.get("GEMINI_API_KEY", ""))

if not api_key:
    st.info("👈 Please enter your Google Gemini API Key in the sidebar to start.")
    st.stop()

# --- NAVIGATION TABS ---
tab_report, tab_map, tab_verify = st.tabs(["📸 Report Civic Issue", "🗺️ Live Ward Map & Feeds", "✅ Authority Resolution Check"])

# ----------------- TAB 1: REPORT ISSUE -----------------
with tab_report:
    st.subheader("Report Infrastructure & Waste Hazard")
    col1, col2 = st.columns([1, 1])

    with col1:
        img_file = st.camera_input("Take a photo of the defect")
        if not img_file:
            img_file = st.file_uploader("Or upload an existing image", type=["jpg", "jpeg", "png"])

        st.caption("Simulate Citizen Location (GPS):")
        selected_ward_idx = st.selectbox("Select Target Ward / Locality", options=range(len(WARDS)), format_func=lambda i: WARDS[i]["name"])
        target_lat = WARDS[selected_ward_idx]["lat"]
        target_lon = WARDS[selected_ward_idx]["lon"]

    with col2:
        if img_file:
            img = Image.open(img_file)
            st.image(img, caption="Incident Capture", use_container_width=True)

            if st.button("🚀 Analyze & Route Issue", type="primary"):
                with st.spinner("AI classifying defect, assessing severity, and querying deduplication engine..."):
                    result = analyze_civic_issue(img, api_key)

                    if not result.get("is_civic_issue", True):
                        st.warning("⚠️ No valid civic issue detected in the uploaded image.")
                    else:
                        cat = result["category"]
                        sev = result["severity"]
                        haz = result["hazard_details"]
                        subj = result["grievance_subject"]
                        body = result["grievance_body"]
                        ward_info = find_nearest_ward(target_lat, target_lon)

                        # Check for duplicates within 30m radius
                        conn = sqlite3.connect("civic_issues.db")
                        c = conn.cursor()
                        c.execute("SELECT id, lat, lon, upvotes FROM issues WHERE category = ? AND status = 'Open'", (cat,))
                        existing = c.fetchall()

                        is_dup = False
                        dup_id = None
                        for row in existing:
                            eid, elat, elon, upvotes = row
                            if geodesic((target_lat, target_lon), (elat, elon)).meters <= 35:
                                is_dup = True
                                dup_id = eid
                                new_upvotes = upvotes + 1
                                c.execute("UPDATE issues SET upvotes = ? WHERE id = ?", (new_upvotes, eid))
                                conn.commit()
                                break

                        if is_dup:
                            st.success(f"⚡ **Spatial Deduplication Active:** This {cat} was already reported nearby! Incremented priority upvotes to {new_upvotes}.")
                        else:
                            c.execute("""
                                INSERT INTO issues (category, severity, description, lat, lon, ward, authority_email, upvotes, status, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'Open', ?)
                            """, (cat, sev, haz, target_lat, target_lon, ward_info["name"], ward_info["email"], datetime.now().strftime("%Y-%m-%d %H:%M")))
                            conn.commit()
                            st.success(f"✅ **New Incident Registered** with Ticket ID #{c.lastrowid}")

                        conn.close()

                        # Display Drafted Action
                        st.markdown(f"**Category:** `{cat}` | **Severity:** `{sev}`")
                        st.markdown(f"**Assigned Department:** {ward_info['authority']}")
                        st.info(f"**Hazard Assessment:** {haz}")

                        # Sen's mailto: intent implementation
                        mailto_url = f"mailto:{ward_info['email']}?subject={subj}&body={body}%0D%0A%0D%0AGPS: {target_lat}, {target_lon}%0D%0AWard: {ward_info['name']}"
                        st.link_button(f"📧 Send Official Grievance to {ward_info['email']}", mailto_url, type="primary")

# ----------------- TAB 2: LIVE WARD MAP -----------------
with tab_map:
    st.subheader("Live Civic Incident Density & Deduplication Map")
    conn = sqlite3.connect("civic_issues.db")
    c = conn.cursor()
    c.execute("SELECT id, category, severity, lat, lon, ward, upvotes, status, description FROM issues")
    rows = c.fetchall()
    conn.close()

    m = folium.Map(location=[12.9716, 77.5946], zoom_start=12)

    color_map = {"Critical": "darkred", "High": "red", "Medium": "orange", "Low": "blue"}

    for r in rows:
        iid, cat, sev, lat, lon, ward, upvotes, status, desc = r
        folium.CircleMarker(
            location=[lat, lon],
            radius=6 + (upvotes * 2),
            popup=f"<b>#{iid} {cat}</b><br>Severity: {sev}<br>Upvotes: {upvotes}<br>Status: {status}<br>{desc}",
            color=color_map.get(sev, "blue"),
            fill=True,
            fill_opacity=0.7
        ).add_to(m)

    st_folium(m, width=900, height=450)

# ----------------- TAB 3: RESOLUTION VERIFICATION -----------------
with tab_verify:
    st.subheader("AI Authority Work-Done Verification")
    st.markdown("Ensure civic contractors do not fraudulently close tickets without actual remediation.")
    
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        before_upload = st.file_uploader("Upload Initial Grievance Photo", type=["jpg", "png"], key="v_before")
    with col_v2:
        after_upload = st.file_uploader("Upload Contractor Resolution Photo", type=["jpg", "png"], key="v_after")

    v_cat = st.selectbox("Issue Category", ["Garbage Dump", "Pothole", "Broken Streetlight", "Open Manhole"])

    if before_upload and after_upload:
        if st.button("🔍 Verify Work Completion"):
            with st.spinner("Analyzing pre- and post-repair states..."):
                b_img = Image.open(before_upload)
                a_img = Image.open(after_upload)
                v_res = verify_resolution(b_img, a_img, v_cat, api_key)

                if v_res.get("is_resolved"):
                    st.success(f"🎉 **Resolution Verified (Confidence: {int(v_res.get('confidence_score', 0.9)*100)}%)**")
                else:
                    st.error("🚨 **Resolution Rejected:** Debris or defect is still visible. Ticket reopened for inspection.")
                st.write(f"**Inspector Notes:** {v_res.get('remarks')}")