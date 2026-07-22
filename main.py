import io
import json
import os
import re
import uuid
import sqlite3
from datetime import date, datetime

import cv2
import numpy as np
import pandas as pd
import qrcode
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image as RLImage
)

from skin_color_analysis import (
    analyze_and_store_skin_color,
    load_latest_skin_color_measurements,
)
from doctor_referral import (
    render_consult_doctor_button,
    render_doctor_dashboard,
    render_hospital_admin_dashboard,
)
from whatsapp_crm import (
    SUPPORTED_LANGUAGES,
    SandboxMessagingProvider,
    render_crm_dashboard,
    send_assessment_package,
    upsert_contact,
)

# ─── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Patient Health Report System",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #1a237e 0%, #0d47a1 50%, #1565c0 100%);
        padding: 2rem;
        border-radius: 12px;
        text-align: center;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 4px 15px rgba(0,0,0,0.2);
    }
    .metric-card {
        background: white;
        border-radius: 10px;
        padding: 1rem;
        text-align: center;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        border-left: 4px solid #1565c0;
    }
    .registration-card {
        background: #ffffff;
        border: 1px solid #dce6f8;
        border-radius: 14px;
        box-shadow: 0 6px 18px rgba(13,71,161,0.10);
        padding: 1.25rem;
        margin-bottom: 1rem;
    }
    .privacy-note {
        background: #e8f5e9;
        border-left: 5px solid #2e7d32;
        border-radius: 8px;
        padding: 0.9rem 1rem;
        color: #1b5e20;
    }
    .section-header {
        color: #1a237e;
        font-size: 1.2rem;
        font-weight: 700;
        border-bottom: 2px solid #1565c0;
        padding-bottom: 0.3rem;
        margin: 1rem 0 0.8rem 0;
    }
    .status-normal { color: #2e7d32; font-weight: 600; }
    .status-high   { color: #c62828; font-weight: 600; }
    .status-low    { color: #e65100; font-weight: 600; }
    .stButton > button {
        background: linear-gradient(135deg, #1565c0, #0d47a1);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.6rem 1.5rem;
        font-weight: 600;
        width: 100%;
    }
    .stButton > button:hover { background: linear-gradient(135deg, #0d47a1, #1a237e); }
    div[data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

# ─── Secure Data Storage (SQLite) ─────────────────────────────────────────────
LEGACY_DATA_FILE = "patients_data.csv"
DATABASE_FILE = "gutvibe_patients.db"
PDF_FOLDER = "patient_reports"
FACE_SCAN_FOLDER = "face_scans"
os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs(FACE_SCAN_FOLDER, exist_ok=True)
os.chmod(FACE_SCAN_FOLDER, 0o700)

COLUMNS = [
    "patient_id", "date", "name", "dob", "age", "gender",
    "mobile", "email", "address", "gps_latitude", "gps_longitude",
    "present_address", "permanent_address",
    "height", "weight", "bmi", "body_fat_pct",
    "hba1c", "cholesterol", "ldl", "hdl", "triglycerides",
    "vitamin_d", "vitamin_b12", "gut_health_score",
    "biological_age", "icmr_risk_score", "hrv",
    "sleep_score", "circadian_score"
]

TEXT_COLUMNS = ", ".join(f"{column} TEXT" for column in COLUMNS)


def secure_database_file():
    """Create the database file with owner-only permissions where supported."""
    if os.path.exists(DATABASE_FILE):
        os.chmod(DATABASE_FILE, 0o600)
        return
    open(DATABASE_FILE, "a", encoding="utf-8").close()
    os.chmod(DATABASE_FILE, 0o600)


def get_connection():
    secure_database_file()
    conn = sqlite3.connect(DATABASE_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"CREATE TABLE IF NOT EXISTS patients ({TEXT_COLUMNS}, PRIMARY KEY(patient_id))")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS face_scans (
            scan_id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL,
            image_path TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            face_count INTEGER NOT NULL,
            FOREIGN KEY(patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(patients)")}
    for column in COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE patients ADD COLUMN {column} TEXT")
    conn.commit()
    return conn


def detect_face_count(image_bytes):
    """Return the number of frontal faces detected in a captured image."""
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unable to read the captured image. Please try again.")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    face_cascade = cv2.CascadeClassifier(cascade_path)
    if face_cascade.empty():
        raise RuntimeError("Face detector could not be loaded.")

    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(60, 60),
    )
    return len(faces)


def save_face_scan(patient_id, image_bytes, face_count):
    """Persist a validated face image and link it to an existing patient."""
    scan_id = str(uuid.uuid4())
    captured_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    safe_patient_id = re.sub(r"[^A-Za-z0-9_-]", "_", patient_id)
    image_filename = f"{safe_patient_id}_{scan_id}.jpg"
    image_path = os.path.join(FACE_SCAN_FOLDER, image_filename)

    with open(image_path, "wb") as image_file:
        image_file.write(image_bytes)
    os.chmod(image_path, 0o600)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO face_scans (scan_id, patient_id, image_path, captured_at, face_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (scan_id, patient_id, image_path, captured_at, face_count),
        )
        conn.commit()
    return scan_id, image_path, captured_at



def get_latest_face_scan(patient_id):
    """Return the latest captured face scan metadata for a patient."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT scan_id, image_path, captured_at, face_count
            FROM face_scans
            WHERE patient_id = ?
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (patient_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "scan_id": row[0],
        "image_path": row[1],
        "captured_at": row[2],
        "face_count": row[3],
    }

def normalize_record(record):
    normalized = {column: str(record.get(column, "") or "") for column in COLUMNS}
    if not normalized["address"]:
        normalized["address"] = normalized.get("present_address", "")
    if not normalized["present_address"]:
        normalized["present_address"] = normalized.get("address", "")
    if not normalized["age"] and normalized["dob"]:
        normalized["age"] = str(calculate_age(normalized["dob"]))
    return normalized


def migrate_legacy_csv():
    if not os.path.exists(LEGACY_DATA_FILE):
        return
    df = pd.read_csv(LEGACY_DATA_FILE, dtype=str).fillna("")
    if df.empty:
        return
    with get_connection() as conn:
        existing = {row[0] for row in conn.execute("SELECT patient_id FROM patients")}
        for _, row in df.iterrows():
            record = normalize_record(row.to_dict())
            if record["patient_id"] and record["patient_id"] not in existing:
                insert_patient(record, conn=conn)
                existing.add(record["patient_id"])


def load_data():
    with get_connection() as conn:
        df = pd.read_sql_query("SELECT * FROM patients ORDER BY patient_id", conn, dtype=str)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNS]


def insert_patient(record, conn=None):
    record = normalize_record(record)
    placeholders = ", ".join("?" for _ in COLUMNS)
    columns = ", ".join(COLUMNS)
    values = [record[column] for column in COLUMNS]
    close_conn = conn is None
    conn = conn or get_connection()
    conn.execute(f"INSERT OR REPLACE INTO patients ({columns}) VALUES ({placeholders})", values)
    conn.commit()
    if close_conn:
        conn.close()


def save_data(df):
    with get_connection() as conn:
        conn.execute("DELETE FROM patients")
        for _, row in df.fillna("").iterrows():
            insert_patient(row.to_dict(), conn=conn)


def calculate_age(dob_value):
    try:
        birth_date = datetime.strptime(str(dob_value), "%Y-%m-%d").date()
    except Exception:
        return ""
    today = date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def generate_patient_id():
    df = load_data()
    if df.empty:
        return "GV-PAT-0001"
    nums = []
    for pid in df["patient_id"].dropna().tolist():
        match = re.search(r"(\d+)$", str(pid))
        if match:
            nums.append(int(match.group(1)))
    next_num = max(nums) + 1 if nums else 1
    return f"GV-PAT-{next_num:04d}"


migrate_legacy_csv()

# ─── Reference Ranges ─────────────────────────────────────────────────────────
RANGES = {
    "hba1c":           (4.0,  5.7,  "% — Normal <5.7 | Pre-diabetic 5.7-6.4 | Diabetic ≥6.5"),
    "cholesterol":     (0,    200,  "mg/dL — Desirable <200 | Borderline 200-239 | High ≥240"),
    "ldl":             (0,    100,  "mg/dL — Optimal <100 | Borderline 130-159 | High ≥160"),
    "hdl":             (60,   100,  "mg/dL — Low <40 men/<50 women | Optimal ≥60"),
    "triglycerides":   (0,    150,  "mg/dL — Normal <150 | Borderline 150-199 | High ≥200"),
    "vitamin_d":       (30,   100,  "ng/mL — Deficient <20 | Insufficient 20-29 | Sufficient ≥30"),
    "vitamin_b12":     (200,  900,  "pg/mL — Deficient <200 | Low 200-300 | Normal 300-900"),
    "gut_health_score":(70,   100,  "Score 0-100 — Optimal ≥70"),
    "icmr_risk_score": (0,    20,   "Score — Low <20 | Moderate 20-40 | High >40"),
    "hrv":             (40,   100,  "ms — Higher is better | Average 40-100"),
    "sleep_score":     (70,   100,  "Score 0-100 — Good ≥70"),
    "circadian_score": (70,   100,  "Score 0-100 — Optimal ≥70"),
    "body_fat_pct":    (5,    25,   "% — Varies by age/gender"),
}

def get_status(field, value):
    if field not in RANGES:
        return "normal", value
    lo, hi, _ = RANGES[field]
    try:
        v = float(value)
        if v < lo:
            return "low", v
        elif v > hi:
            return "high", v
        return "normal", v
    except Exception:
        return "normal", value

# ─── QR Code ──────────────────────────────────────────────────────────────────
def generate_qr(data_dict):
    summary = {
        "ID":    data_dict.get("patient_id",""),
        "Name":  data_dict.get("name",""),
        "Date":  data_dict.get("date",""),
        "HbA1c": data_dict.get("hba1c",""),
        "BMI":   data_dict.get("bmi",""),
    }
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=2)
    qr.add_data(json.dumps(summary))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ─── PDF Report ───────────────────────────────────────────────────────────────
def build_pdf(row: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )
    styles = getSampleStyleSheet()
    C_BLUE  = colors.HexColor("#1a237e")
    C_LBLUE = colors.HexColor("#e3f2fd")
    C_GRAY  = colors.HexColor("#f5f5f5")
    C_RED   = colors.HexColor("#c62828")
    C_GREEN = colors.HexColor("#2e7d32")
    C_ORG   = colors.HexColor("#e65100")

    title_style = ParagraphStyle("title", fontSize=20, textColor=colors.white,
                                  alignment="center",fontName="Helvetica-Bold", leading=24)
    sub_style   = ParagraphStyle("sub",   fontSize=11, textColor=colors.white,
                                  alignment="center",fontName="Helvetica", leading=14)
    sec_style   = ParagraphStyle("sec",   fontSize=12, textColor=C_BLUE,
                                  fontName="Helvetica-Bold", leading=16, spaceAfter=4)
    norm_style  = ParagraphStyle("norm",  fontSize=9,  fontName="Helvetica", leading=12)
    small_style = ParagraphStyle("small", fontSize=8,  fontName="Helvetica", textColor=colors.grey)

    story = []

    # ── Header ──
    header_data = [[
        Paragraph("🏥  PATIENT HEALTH REPORT", title_style),
    ]]
    header_table = Table(header_data, colWidths=[18*cm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), C_BLUE),
        ("ROUNDEDCORNERS", (0,0), (-1,-1), [8,8,8,8]),
        ("BOTTOMPADDING", (0,0), (-1,-1), 14),
        ("TOPPADDING",    (0,0), (-1,-1), 14),
    ]))
    story.append(header_table)

    sub_data = [[Paragraph(f"Report Date: {row.get('date','')}   |   Patient ID: {row.get('patient_id','')}", sub_style)]]
    sub_table = Table(sub_data, colWidths=[18*cm])
    sub_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor("#1565c0")),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
    ]))
    story.append(sub_table)
    story.append(Spacer(1, 12))

    # ── Patient Info ──
    story.append(Paragraph("PATIENT INFORMATION", sec_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_BLUE, spaceAfter=6))

    # Build age
    try:
        dob = datetime.strptime(row.get("dob",""), "%Y-%m-%d")
        age = (datetime.today() - dob).days // 365
        age_str = f"{age} years"
    except Exception:
        age_str = row.get("dob","")

    info_data = [
        ["Full Name",        row.get("name",""),       "Date of Birth / Age",    f"{row.get('dob','')} / {row.get('age', age_str)}"],
        ["Gender",           row.get("gender",""),     "Report Date",      row.get("date","")],
        ["Mobile",           row.get("mobile",""),     "Email",            row.get("email","")],
        ["Address",          row.get("address", row.get("present_address","")),  "GPS", f"{row.get('gps_latitude','')}, {row.get('gps_longitude','')}"],
    ]
    info_table = Table(info_data, colWidths=[3.5*cm, 5.5*cm, 3.5*cm, 5.5*cm])
    info_table.setStyle(TableStyle([
        ("FONTNAME",     (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE",     (0,0), (-1,-1), 9),
        ("FONTNAME",     (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",     (2,0), (2,-1), "Helvetica-Bold"),
        ("TEXTCOLOR",    (0,0), (0,-1), C_BLUE),
        ("TEXTCOLOR",    (2,0), (2,-1), C_BLUE),
        ("BACKGROUND",   (0,0), (-1,-1), C_GRAY),
        ("ROWBACKGROUNDS",(0,0),(-1,-1), [C_GRAY, C_LBLUE]),
        ("GRID",         (0,0), (-1,-1), 0.5, colors.lightgrey),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",   (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
        ("LEFTPADDING",  (0,0), (-1,-1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 12))

    # ── Body Metrics ──
    story.append(Paragraph("BODY METRICS", sec_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_BLUE, spaceAfter=6))

    def status_color(field, val):
        st_code, _ = get_status(field, val)
        if st_code == "high": return C_RED
        if st_code == "low":  return C_ORG
        return C_GREEN

    def bmi_category(bmi):
        try:
            b = float(bmi)
            if b < 18.5: return "Underweight"
            elif b < 25:  return "Normal"
            elif b < 30:  return "Overweight"
            else:         return "Obese"
        except Exception:
            return ""

    body_data = [
        ["Metric", "Value", "Unit", "Status"],
        ["Height",      row.get("height",""),       "cm",     "—"],
        ["Weight",      row.get("weight",""),       "kg",     "—"],
        ["BMI",         row.get("bmi",""),          "kg/m²",  bmi_category(row.get("bmi",""))],
        ["Body Fat %",  row.get("body_fat_pct",""), "%",      "—"],
        ["Biological Age", row.get("biological_age",""), "years", "—"],
    ]
    bm_table = Table(body_data, colWidths=[5*cm, 3.5*cm, 3*cm, 6.5*cm])
    bm_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), C_BLUE),
        ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, C_LBLUE]),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.lightgrey),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("ALIGN",         (1,0), (2,-1), "CENTER"),
    ]))
    story.append(bm_table)
    story.append(Spacer(1, 12))

    # ── Lab Results ──
    story.append(Paragraph("LAB RESULTS & BIOMARKERS", sec_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_BLUE, spaceAfter=6))

    lab_fields = [
        ("HbA1c",        "hba1c",        "%"),
        ("Total Cholesterol","cholesterol","mg/dL"),
        ("LDL",          "ldl",          "mg/dL"),
        ("HDL",          "hdl",          "mg/dL"),
        ("Triglycerides","triglycerides","mg/dL"),
        ("Vitamin D",    "vitamin_d",    "ng/mL"),
        ("Vitamin B12",  "vitamin_b12",  "pg/mL"),
    ]
    lab_data = [["Biomarker", "Value", "Unit", "Reference Range", "Status"]]
    lab_colors = []
    for label, field, unit in lab_fields:
        val = row.get(field,"")
        st_code, _ = get_status(field, val)
        _, _, ref = RANGES.get(field, ("","","—"))
        ref_short = ref.split(" — ")[1] if " — " in ref else ref
        status_txt = st_code.upper()
        lab_data.append([label, val, unit, ref_short, status_txt])
        lab_colors.append((st_code, len(lab_data)-1))

    lab_table = Table(lab_data, colWidths=[3.5*cm, 2.5*cm, 2*cm, 6.5*cm, 3.5*cm])
    ts = [
        ("BACKGROUND",    (0,0), (-1,0), C_BLUE),
        ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, C_LBLUE]),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.lightgrey),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("ALIGN",         (1,0), (2,-1), "CENTER"),
        ("ALIGN",         (4,0), (4,-1), "CENTER"),
    ]
    for st_code, row_idx in lab_colors:
        if st_code == "high":
            ts.append(("TEXTCOLOR", (4,row_idx), (4,row_idx), C_RED))
            ts.append(("FONTNAME",  (4,row_idx), (4,row_idx), "Helvetica-Bold"))
        elif st_code == "low":
            ts.append(("TEXTCOLOR", (4,row_idx), (4,row_idx), C_ORG))
            ts.append(("FONTNAME",  (4,row_idx), (4,row_idx), "Helvetica-Bold"))
        else:
            ts.append(("TEXTCOLOR", (4,row_idx), (4,row_idx), C_GREEN))
    lab_table.setStyle(TableStyle(ts))
    story.append(lab_table)
    story.append(Spacer(1, 12))

    # ── Wellness Scores ──
    story.append(Paragraph("WELLNESS & LIFESTYLE SCORES", sec_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=C_BLUE, spaceAfter=6))

    wellness_fields = [
        ("Gut Health Score",  "gut_health_score",  "/100"),
        ("ICMR Risk Score",   "icmr_risk_score",   "/100"),
        ("HRV",               "hrv",               "ms"),
        ("Sleep Score",       "sleep_score",       "/100"),
        ("Circadian Score",   "circadian_score",   "/100"),
    ]
    w_data = [["Wellness Metric", "Score", "Unit", "Reference", "Status"]]
    w_colors = []
    for label, field, unit in wellness_fields:
        val = row.get(field,"")
        st_code, _ = get_status(field, val)
        _, _, ref = RANGES.get(field, ("","","—"))
        ref_short = ref.split(" — ")[1] if " — " in ref else ref
        w_data.append([label, val, unit, ref_short, st_code.upper()])
        w_colors.append((st_code, len(w_data)-1))

    w_table = Table(w_data, colWidths=[4.5*cm, 2.5*cm, 2*cm, 5.5*cm, 3.5*cm])
    wts = [
        ("BACKGROUND",    (0,0), (-1,0), C_BLUE),
        ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [colors.white, C_LBLUE]),
        ("GRID",          (0,0), (-1,-1), 0.5, colors.lightgrey),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("ALIGN",         (1,0), (2,-1), "CENTER"),
        ("ALIGN",         (4,0), (4,-1), "CENTER"),
    ]
    for st_code, row_idx in w_colors:
        if st_code == "high":
            wts.append(("TEXTCOLOR", (4,row_idx), (4,row_idx), C_RED))
            wts.append(("FONTNAME",  (4,row_idx), (4,row_idx), "Helvetica-Bold"))
        elif st_code == "low":
            wts.append(("TEXTCOLOR", (4,row_idx), (4,row_idx), C_ORG))
            wts.append(("FONTNAME",  (4,row_idx), (4,row_idx), "Helvetica-Bold"))
        else:
            wts.append(("TEXTCOLOR", (4,row_idx), (4,row_idx), C_GREEN))
    w_table.setStyle(TableStyle(wts))
    story.append(w_table)
    story.append(Spacer(1, 16))

    # ── QR Code ──
    qr_buf = generate_qr(row)
    qr_img = RLImage(qr_buf, width=3*cm, height=3*cm)
    qr_data = [[
        Paragraph("Scan QR code for quick patient summary", small_style),
        qr_img
    ]]
    qr_table = Table(qr_data, colWidths=[14.5*cm, 3.5*cm])
    qr_table.setStyle(TableStyle([
        ("VALIGN",  (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN",   (1,0), (1,0), "RIGHT"),
        ("BACKGROUND", (0,0), (-1,-1), C_GRAY),
        ("ROUNDEDCORNERS", (0,0), (-1,-1), [6,6,6,6]),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
    ]))
    story.append(qr_table)
    story.append(Spacer(1, 8))

    # ── Footer ──
    footer_data = [[
        Paragraph("This report is computer-generated. Please consult your healthcare provider for interpretation.", small_style),
        Paragraph(f"Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}", small_style)
    ]]
    footer_table = Table(footer_data, colWidths=[13*cm, 5*cm])
    footer_table.setStyle(TableStyle([
        ("TEXTCOLOR",    (0,0), (-1,-1), colors.grey),
        ("FONTSIZE",     (0,0), (-1,-1), 7),
        ("ALIGN",        (1,0), (1,0), "RIGHT"),
        ("TOPPADDING",   (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("LINEABOVE",    (0,0), (-1,0), 0.5, colors.lightgrey),
    ]))
    story.append(footer_table)

    doc.build(story)
    buf.seek(0)
    return buf.read()

# ─── Sidebar Navigation ───────────────────────────────────────────────────────
st.sidebar.markdown("""
<div style='text-align:center; padding:1rem 0;'>
    <h2 style='color:#1a237e; margin:0;'>🏥 HealthTrack</h2>
    <p style='color:#666; font-size:0.85rem; margin:0;'>Patient Report System</p>
</div>
""", unsafe_allow_html=True)

page = st.sidebar.radio(
    "Navigation",
    ["🆕 Patient Registration", "📋 Add Patient", "📷 Face Scan", "🎨 Skin & Color Analysis", "🔍 View / Search", "💬 WhatsApp CRM", "🩺 Doctor Dashboard", "🏥 Hospital Admin", "📊 Analytics", "📁 All Reports"],
    label_visibility="collapsed"
)
st.sidebar.markdown("---")
df_all = load_data()
st.sidebar.metric("Total Patients", len(df_all))
if not df_all.empty:
    st.sidebar.metric("Latest Entry", df_all.iloc[-1]["name"] if "name" in df_all.columns else "—")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — PATIENT REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════
if page == "🆕 Patient Registration":
    st.markdown("""
    <div class='main-header'>
        <h1>🆕 GutVibe Patient Registration</h1>
        <p>Register patient demographics, contact details, GPS location, and body metrics in the secure database</p>
    </div>
    """, unsafe_allow_html=True)

    new_id = generate_patient_id()
    st.markdown(f"<div class='registration-card'><h3>Auto-generated Patient ID: {new_id}</h3><p>This ID is reserved when the registration is saved.</p></div>", unsafe_allow_html=True)
    st.markdown("<div class='privacy-note'>🔒 Records are stored in the local SQLite database with owner-only file permissions. Use approved deployment controls for production PHI handling.</div>", unsafe_allow_html=True)

    with st.form("registration_form", clear_on_submit=False):
        st.markdown("<div class='section-header'>👤 Identity & Contact</div>", unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            name = st.text_input("Full Name *", placeholder="Patient full name")
            dob = st.date_input("Date of Birth *", min_value=date(1900, 1, 1), max_value=date.today(), key="registration_dob")
            age = calculate_age(str(dob))
            st.number_input("Age (auto-calculated)", value=int(age or 0), disabled=True)
        with c2:
            gender = st.selectbox("Gender *", ["Female", "Male", "Other", "Prefer not to say"], key="registration_gender")
            mobile = st.text_input("Mobile *", placeholder="10-digit mobile number")
            email = st.text_input("Email", placeholder="patient@example.com")
        with c3:
            registration_date = st.date_input("Registration Date *", value=date.today())
            address = st.text_area("Address *", height=96, placeholder="Street, city, state, ZIP")

        st.markdown("<div class='section-header'>📍 GPS Location</div>", unsafe_allow_html=True)
        g1, g2 = st.columns(2)
        with g1:
            gps_latitude = st.number_input("Latitude", min_value=-90.0, max_value=90.0, value=0.0, step=0.000001, format="%.6f")
        with g2:
            gps_longitude = st.number_input("Longitude", min_value=-180.0, max_value=180.0, value=0.0, step=0.000001, format="%.6f")

        st.markdown("<div class='section-header'>⚖️ Body Metrics</div>", unsafe_allow_html=True)
        b1, b2, b3 = st.columns(3)
        with b1:
            height = st.number_input("Height (cm) *", 50.0, 250.0, 165.0, 0.1, key="registration_height")
        with b2:
            weight = st.number_input("Weight (kg) *", 10.0, 300.0, 65.0, 0.1, key="registration_weight")
        with b3:
            bmi = round(weight / ((height / 100) ** 2), 1) if height > 0 else 0.0
            st.number_input("BMI (auto-calculated)", value=bmi, format="%.1f", disabled=True, key="registration_bmi")

        registered = st.form_submit_button("🔐 Register Patient", use_container_width=True)

    if registered:
        mobile_clean = re.sub(r"\D", "", mobile)
        if not name.strip() or not address.strip() or not mobile_clean:
            st.error("Full name, mobile, and address are required.")
        elif email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            st.error("Please enter a valid email address or leave it blank.")
        else:
            record = {
                "patient_id": new_id,
                "date": str(registration_date),
                "name": name.strip(),
                "dob": str(dob),
                "age": age,
                "gender": gender,
                "mobile": mobile.strip(),
                "email": email.strip(),
                "address": address.strip(),
                "gps_latitude": f"{gps_latitude:.6f}",
                "gps_longitude": f"{gps_longitude:.6f}",
                "present_address": address.strip(),
                "permanent_address": address.strip(),
                "height": height,
                "weight": weight,
                "bmi": bmi,
            }
            insert_patient(record)
            st.success(f"✅ Patient **{name.strip()}** registered securely with ID **{new_id}**.")
            st.markdown("### Registration Summary")
            st.dataframe(pd.DataFrame([normalize_record(record)])[ ["patient_id", "name", "dob", "age", "gender", "mobile", "email", "address", "gps_latitude", "gps_longitude", "height", "weight", "bmi"] ], use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — ADD PATIENT
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📋 Add Patient":
    st.markdown("""
    <div class='main-header'>
        <h1>🏥 Patient Health Report System</h1>
        <p>Add new patient & generate PDF report with QR code</p>
    </div>
    """, unsafe_allow_html=True)

    new_id = generate_patient_id()
    st.info(f"New Patient ID: **{new_id}**")

    with st.form("patient_form", clear_on_submit=False):

        # ── Demographics ──
        st.markdown("<div class='section-header'>👤 Patient Demographics</div>", unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            name = st.text_input("Full Name *", placeholder="e.g. Rajesh Kumar")
            gender = st.selectbox("Gender *", ["Male", "Female", "Other"])
        with c2:
            dob = st.date_input("Date of Birth *", min_value=date(1900,1,1), max_value=date.today())
            report_date = st.date_input("Report Date *", value=date.today())
        with c3:
            present_address = st.text_area("Present Address", height=70)
            permanent_address = st.text_area("Permanent Address", height=70)

        st.markdown("<div class='section-header'>💬 WhatsApp Follow-up</div>", unsafe_allow_html=True)
        w1, w2, w3 = st.columns(3)
        with w1: whatsapp_mobile = st.text_input("WhatsApp mobile", placeholder="Country code + number")
        with w2: whatsapp_language = st.selectbox("WhatsApp language", SUPPORTED_LANGUAGES)
        with w3: whatsapp_opt_in = st.checkbox("Patient opts in to WhatsApp wellness messages")

        # ── Body Metrics ──
        st.markdown("<div class='section-header'>⚖️ Body Metrics</div>", unsafe_allow_html=True)
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1: height = st.number_input("Height (cm)", 50.0, 250.0, 165.0, 0.1)
        with c2: weight = st.number_input("Weight (kg)", 10.0, 300.0, 65.0, 0.1)
        with c3:
            bmi_calc = round(weight / ((height/100)**2), 1) if height > 0 else 0.0
            bmi = st.number_input("BMI (auto-calculated)", value=bmi_calc, format="%.1f", disabled=False)
        with c4: body_fat = st.number_input("Body Fat %", 1.0, 70.0, 20.0, 0.1)
        with c5: bio_age = st.number_input("Biological Age (yrs)", 1, 120, 35)

        # ── Lab Results ──
        st.markdown("<div class='section-header'>🧪 Lab Results</div>", unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        with c1: hba1c = st.number_input("HbA1c (%)", 3.0, 20.0, 5.5, 0.1)
        with c2: cholesterol = st.number_input("Cholesterol (mg/dL)", 50, 500, 180)
        with c3: ldl = st.number_input("LDL (mg/dL)", 10, 400, 90)
        with c4: hdl = st.number_input("HDL (mg/dL)", 10, 200, 65)

        c1, c2, c3 = st.columns(3)
        with c1: triglycerides = st.number_input("Triglycerides (mg/dL)", 20, 1000, 130)
        with c2: vitamin_d = st.number_input("Vitamin D (ng/mL)", 1.0, 200.0, 35.0, 0.1)
        with c3: vitamin_b12 = st.number_input("Vitamin B12 (pg/mL)", 50, 2000, 400)

        # ── Wellness Scores ──
        st.markdown("<div class='section-header'>💪 Wellness & Lifestyle Scores</div>", unsafe_allow_html=True)
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1: gut_health = st.slider("Gut Health Score", 0, 100, 75)
        with c2: icmr_risk = st.slider("ICMR Risk Score", 0, 100, 15)
        with c3: hrv = st.number_input("HRV (ms)", 5, 200, 65)
        with c4: sleep_score = st.slider("Sleep Score", 0, 100, 78)
        with c5: circadian_score = st.slider("Circadian Score", 0, 100, 80)

        st.markdown("---")
        submitted = st.form_submit_button("💾 Save Patient & Generate Report", use_container_width=True)

    if submitted:
        if not name.strip():
            st.error("Patient name is required!")
        else:
            record = {
                "patient_id":        new_id,
                "date":              str(report_date),
                "name":              name.strip(),
                "dob":               str(dob),
                "age":               calculate_age(str(dob)),
                "gender":            gender,
                "mobile":            whatsapp_mobile.strip(),
                "address":           present_address,
                "present_address":   present_address,
                "permanent_address": permanent_address,
                "height":            height,
                "weight":            weight,
                "bmi":               round(weight/((height/100)**2),1),
                "body_fat_pct":      body_fat,
                "hba1c":             hba1c,
                "cholesterol":       cholesterol,
                "ldl":               ldl,
                "hdl":               hdl,
                "triglycerides":     triglycerides,
                "vitamin_d":         vitamin_d,
                "vitamin_b12":       vitamin_b12,
                "gut_health_score":  gut_health,
                "biological_age":    bio_age,
                "icmr_risk_score":   icmr_risk,
                "hrv":               hrv,
                "sleep_score":       sleep_score,
                "circadian_score":   circadian_score,
            }

            # Save to secure SQLite database
            insert_patient(record)

            # Generate and save PDF
            pdf_bytes = build_pdf(record)
            pdf_path = os.path.join(PDF_FOLDER, f"{new_id}_{name.replace(' ','_')}.pdf")
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)

            if whatsapp_opt_in and whatsapp_mobile.strip():
                upsert_contact(new_id, whatsapp_mobile.strip(), whatsapp_language, opt_in=True,
                               kiosk_session_id=f"kiosk-{new_id}")
                public_base = os.getenv("GUTVIBE_PUBLIC_URL", "https://reports.gutvibe.example")
                send_assessment_package(
                    record,
                    SandboxMessagingProvider(),
                    f"{public_base}/reports/{new_id}.pdf",
                    f"{public_base}/reports/{new_id}/qr",
                )

            st.success(f"✅ Patient **{name}** saved with ID **{new_id}**!")

            col1, col2 = st.columns(2)
            with col1:
                st.download_button(
                    "📥 Download PDF Report",
                    data=pdf_bytes,
                    file_name=f"{new_id}_{name.replace(' ','_')}_report.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            with col2:
                qr_buf = generate_qr(record)
                st.download_button(
                    "📲 Download QR Code",
                    data=qr_buf.getvalue(),
                    file_name=f"{new_id}_qr.png",
                    mime="image/png",
                    use_container_width=True
                )

            # Preview metrics
            st.markdown("### 📊 Quick Metrics Preview")
            cols = st.columns(5)
            metrics = [
                ("BMI", record["bmi"], "kg/m²"),
                ("HbA1c", record["hba1c"], "%"),
                ("Cholesterol", record["cholesterol"], "mg/dL"),
                ("Sleep Score", record["sleep_score"], "/100"),
                ("HRV", record["hrv"], "ms"),
            ]
            for col, (label, val, unit) in zip(cols, metrics):
                col.metric(label, f"{val} {unit}")

            st.markdown("### Wellness Report Follow-up")
            render_consult_doctor_button(record)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — FACE SCAN CAPTURE
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📷 Face Scan":
    st.markdown("""
    <div class='main-header'>
        <h1>📷 Face Scan Capture</h1>
        <p>Capture and store one validated face image for the selected patient. AI analysis is not performed in this phase.</p>
    </div>
    """, unsafe_allow_html=True)

    df = load_data()
    if df.empty:
        st.info("No patient records yet. Register or add a patient before capturing a face scan.")
        st.stop()

    st.markdown("<div class='privacy-note'>🔒 Face images are saved to a local restricted folder and linked to the selected patient ID. No AI analysis is run.</div>", unsafe_allow_html=True)

    patient_labels = df.apply(lambda r: f"{r['patient_id']} — {r['name']} ({r['date']})", axis=1).tolist()
    selected_patient = st.selectbox("Select current patient", patient_labels)
    selected_index = patient_labels.index(selected_patient)
    patient_row = df.iloc[selected_index].to_dict()
    patient_id = patient_row["patient_id"]

    st.markdown("### Open device camera")
    st.caption("Allow camera access in your browser, center exactly one face in the frame, then click Take Photo.")
    captured_image = st.camera_input("Capture face image", key=f"face_capture_{patient_id}")

    if captured_image is not None:
        image_bytes = captured_image.getvalue()
        st.image(image_bytes, caption="Captured face image preview", use_container_width=True)

        if st.button("🔐 Validate and Save Face Scan", use_container_width=True):
            try:
                face_count = detect_face_count(image_bytes)
                if face_count == 0:
                    st.error("No face was detected. Please retake the photo with the patient's face clearly visible.")
                elif face_count > 1:
                    st.error("Multiple faces were detected. Please retake the photo with only the selected patient in frame.")
                else:
                    scan_id, image_path, captured_at = save_face_scan(patient_id, image_bytes, face_count)
                    st.success(f"✅ Face scan saved for patient **{patient_id}**. Scan ID: **{scan_id}**")
                    st.caption(f"Stored securely at {image_path} on {captured_at}.")
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — SKIN & COLOR ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🎨 Skin & Color Analysis":
    st.markdown("""
    <div class='main-header'>
        <h1>🎨 Skin & Color Analysis</h1>
        <p>Numerical, non-diagnostic skin color measurements from the latest captured face image only</p>
    </div>
    """, unsafe_allow_html=True)

    df = load_data()
    if df.empty:
        st.info("No patient records yet. Register or add a patient before running analysis.")
        st.stop()

    st.markdown("<div class='privacy-note'>🔒 This analysis uses only the selected patient's stored face scan and reports numerical measurements and charts only. It does not diagnose disease or medical conditions.</div>", unsafe_allow_html=True)

    patient_labels = df.apply(lambda r: f"{r['patient_id']} — {r['name']} ({r['date']})", axis=1).tolist()
    selected_patient = st.selectbox("Select patient", patient_labels, key="skin_color_patient")
    selected_index = patient_labels.index(selected_patient)
    patient_row = df.iloc[selected_index].to_dict()
    patient_id = patient_row["patient_id"]

    latest_scan = get_latest_face_scan(patient_id)
    if latest_scan is None:
        st.info("No captured face scan is available for this patient. Capture and save a face scan first.")
        st.stop()

    st.dataframe(
        pd.DataFrame([{
            "patient_id": patient_id,
            "scan_id": latest_scan["scan_id"],
            "captured_at": latest_scan["captured_at"],
            "face_count": latest_scan["face_count"],
        }]),
        use_container_width=True,
        hide_index=True,
    )

    if st.button("📊 Analyze Latest Captured Face Scan", use_container_width=True):
        try:
            with open(latest_scan["image_path"], "rb") as image_file:
                image_bytes = image_file.read()
            measurement_id, analysis = analyze_and_store_skin_color(
                patient_id=patient_id,
                image_bytes=image_bytes,
                scan_id=latest_scan["scan_id"],
            )
            st.success(f"✅ Skin and color measurements saved. Measurement ID: {measurement_id}")
            st.session_state[f"skin_color_latest_{patient_id}"] = {
                "measurement_id": measurement_id,
                "scan_id": latest_scan["scan_id"],
                "analyzed_at": "just now",
                "measurements": analysis.measurements.__dict__,
                "analysis_note": analysis.analysis_note,
            }
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(str(exc))

    result = st.session_state.get(f"skin_color_latest_{patient_id}") or load_latest_skin_color_measurements(patient_id)
    if result:
        measurements = result["measurements"]
        st.markdown("### Numerical Measurements")
        metrics_df = pd.DataFrame([
            {"Measurement": key, "Value": value}
            for key, value in measurements.items()
            if isinstance(value, (int, float))
        ])
        st.dataframe(metrics_df, use_container_width=True, hide_index=True)

        chart_df = metrics_df.set_index("Measurement")
        st.markdown("### Measurement Chart")
        st.bar_chart(chart_df)

        st.markdown("### Overall Skin Tone Channels")
        tone_df = pd.DataFrame({
            "Channel": ["L*", "a*", "b*", "R", "G", "B"],
            "Value": [
                measurements.get("overall_skin_tone_l"),
                measurements.get("overall_skin_tone_a"),
                measurements.get("overall_skin_tone_b"),
                measurements.get("overall_skin_rgb_r"),
                measurements.get("overall_skin_rgb_g"),
                measurements.get("overall_skin_rgb_b"),
            ],
        }).set_index("Channel")
        st.bar_chart(tone_df)
        st.caption(result["analysis_note"])

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — VIEW / SEARCH
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🔍 View / Search":
    st.markdown("<div class='main-header'><h1>🔍 Patient Records</h1><p>Search, view and download patient reports</p></div>", unsafe_allow_html=True)

    df = load_data()
    if df.empty:
        st.info("No patient records yet. Add a patient first.")
        st.stop()

    search = st.text_input("🔍 Search by Name or Patient ID", placeholder="e.g. Rajesh or PAT-0001")
    if search:
        mask = (df["name"].str.contains(search, case=False, na=False) |
                df["patient_id"].str.contains(search, case=False, na=False))
        df_show = df[mask]
    else:
        df_show = df

    st.markdown(f"**{len(df_show)} record(s) found**")
    st.dataframe(
        df_show[["patient_id","date","name","age","gender","mobile","email","bmi","hba1c","cholesterol","sleep_score","hrv"]],
        use_container_width=True, hide_index=True
    )

    if not df_show.empty:
        st.markdown("### 📄 Generate Report for a Patient")
        names = df_show.apply(lambda r: f"{r['patient_id']} — {r['name']} ({r['date']})", axis=1).tolist()
        sel = st.selectbox("Select patient", names)
        idx = names.index(sel)
        row = df_show.iloc[idx].to_dict()

        col1, col2 = st.columns(2)
        with col1:
            if st.button("📥 Download PDF Report", use_container_width=True):
                pdf_bytes = build_pdf(row)
                st.download_button(
                    "Click to download",
                    data=pdf_bytes,
                    file_name=f"{row['patient_id']}_report.pdf",
                    mime="application/pdf"
                )
        with col2:
            if st.button("📲 Download QR Code", use_container_width=True):
                qr_buf = generate_qr(row)
                st.download_button(
                    "Click to download QR",
                    data=qr_buf.getvalue(),
                    file_name=f"{row['patient_id']}_qr.png",
                    mime="image/png"
                )

        # Detail view
        with st.expander("🔎 View Full Patient Details"):
            c1, c2 = st.columns(2)
            with c1:
                st.write("**Patient ID:**", row.get("patient_id"))
                st.write("**Name:**",       row.get("name"))
                st.write("**DOB:**",        row.get("dob"))
                st.write("**Age:**",        row.get("age"))
                st.write("**Gender:**",     row.get("gender"))
                st.write("**Mobile:**",     row.get("mobile"))
                st.write("**Email:**",      row.get("email"))
                st.write("**Address:**",    row.get("address", row.get("present_address")))
                st.write("**GPS:**",        f"{row.get('gps_latitude','')}, {row.get('gps_longitude','')}")
                st.write("**Height:**",     row.get("height"), "cm")
                st.write("**Weight:**",     row.get("weight"), "kg")
                st.write("**BMI:**",        row.get("bmi"))
                st.write("**Body Fat:**",   row.get("body_fat_pct"), "%")
            with c2:
                st.write("**HbA1c:**",      row.get("hba1c"), "%")
                st.write("**Cholesterol:**",row.get("cholesterol"), "mg/dL")
                st.write("**LDL:**",        row.get("ldl"), "mg/dL")
                st.write("**HDL:**",        row.get("hdl"), "mg/dL")
                st.write("**Vitamin D:**",  row.get("vitamin_d"), "ng/mL")
                st.write("**Sleep Score:**",row.get("sleep_score"))
                st.write("**HRV:**",        row.get("hrv"), "ms")
                st.write("**ICMR Risk:**",  row.get("icmr_risk_score"))

        st.markdown("### Wellness Report Follow-up")
        render_consult_doctor_button(row)

# ═══════════════════════════════════════════════════════════════════════════════
# DOCTOR REFERRAL DASHBOARDS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🩺 Doctor Dashboard":
    render_doctor_dashboard()

elif page == "💬 WhatsApp CRM":
    render_crm_dashboard()

elif page == "🏥 Hospital Admin":
    render_hospital_admin_dashboard()

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📊 Analytics":
    st.markdown("<div class='main-header'><h1>📊 Population Analytics</h1><p>Insights across all patient records</p></div>", unsafe_allow_html=True)

    df = load_data()
    if df.empty:
        st.info("No data yet.")
        st.stop()

    numeric_cols = ["bmi","hba1c","cholesterol","ldl","hdl","triglycerides",
                    "vitamin_d","vitamin_b12","gut_health_score","biological_age",
                    "icmr_risk_score","hrv","sleep_score","circadian_score","body_fat_pct"]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Summary cards
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Patients", len(df))
    c2.metric("Avg BMI", f"{df['bmi'].mean():.1f}" if not df['bmi'].isna().all() else "—")
    c3.metric("Avg HbA1c", f"{df['hba1c'].mean():.1f}%" if not df['hba1c'].isna().all() else "—")
    c4.metric("Avg Sleep Score", f"{df['sleep_score'].mean():.0f}/100" if not df['sleep_score'].isna().all() else "—")

    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Gender Distribution")
        if "gender" in df.columns:
            gd = df["gender"].value_counts().reset_index()
            gd.columns = ["Gender", "Count"]
            st.dataframe(gd, use_container_width=True, hide_index=True)

    with col2:
        st.markdown("#### BMI Category Distribution")
        if not df["bmi"].isna().all():
            def bmi_cat(b):
                if pd.isna(b): return "Unknown"
                if b < 18.5: return "Underweight"
                elif b < 25: return "Normal"
                elif b < 30: return "Overweight"
                return "Obese"
            bc = df["bmi"].apply(bmi_cat).value_counts().reset_index()
            bc.columns = ["Category","Count"]
            st.dataframe(bc, use_container_width=True, hide_index=True)

    st.markdown("#### 📋 Population Statistics Summary")
    stats = df[numeric_cols].describe().round(2)
    st.dataframe(stats, use_container_width=True)

    st.markdown("#### 📈 Download All Data as CSV")
    csv_bytes = df.to_csv(index=False).encode()
    st.download_button("📥 Download CSV", data=csv_bytes,
                       file_name="all_patients.csv", mime="text/csv")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — ALL REPORTS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📁 All Reports":
    st.markdown("<div class='main-header'><h1>📁 All Patient Reports</h1><p>Bulk PDF generation and download</p></div>", unsafe_allow_html=True)

    df = load_data()
    if df.empty:
        st.info("No patient records yet.")
        st.stop()

    st.markdown(f"**{len(df)} patient records** — generate individual or bulk PDFs")

    if st.button("🗂️ Generate & Download All PDFs as ZIP", use_container_width=True):
        import zipfile
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            prog = st.progress(0)
            for i, (_, row) in enumerate(df.iterrows()):
                row_dict = row.to_dict()
                pdf_bytes = build_pdf(row_dict)
                fname = f"{row_dict.get('patient_id','PAT')}_{str(row_dict.get('name','')).replace(' ','_')}.pdf"
                zf.writestr(fname, pdf_bytes)
                prog.progress((i+1)/len(df))
        zip_buf.seek(0)
        st.download_button(
            "📦 Download ZIP",
            data=zip_buf.getvalue(),
            file_name="all_patient_reports.pdf.zip",
            mime="application/zip",
            use_container_width=True
        )

    st.markdown("---")
    st.markdown("### Generate individual report")
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        pid  = row_dict.get("patient_id","")
        name = row_dict.get("name","")
        dt   = row_dict.get("date","")
        with st.expander(f"📄 {pid} — {name}  ({dt})"):
            c1, c2 = st.columns(2)
            with c1:
                if st.button(f"Generate PDF — {pid}", key=f"pdf_{pid}", use_container_width=True):
                    pdf_bytes = build_pdf(row_dict)
                    st.download_button(
                        "📥 Download",
                        data=pdf_bytes,
                        file_name=f"{pid}_report.pdf",
                        mime="application/pdf",
                        key=f"dl_{pid}"
                    )
            with c2:
                qr_buf = generate_qr(row_dict)
                st.download_button(
                    "📲 QR Code",
                    data=qr_buf.getvalue(),
                    file_name=f"{pid}_qr.png",
                    mime="image/png",
                    key=f"qr_{pid}",
                    use_container_width=True
                )
