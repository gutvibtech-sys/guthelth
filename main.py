import io
import json
import os
from datetime import date, datetime

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

# ─── Data Storage (CSV) ───────────────────────────────────────────────────────
DATA_FILE = "patients_data.csv"
PDF_FOLDER = "patient_reports"
os.makedirs(PDF_FOLDER, exist_ok=True)

COLUMNS = [
    "patient_id","date","name","dob","gender",
    "present_address","permanent_address",
    "height","weight","bmi","body_fat_pct",
    "hba1c","cholesterol","ldl","hdl","triglycerides",
    "vitamin_d","vitamin_b12","gut_health_score",
    "biological_age","icmr_risk_score","hrv",
    "sleep_score","circadian_score"
]

def load_data():
    if os.path.exists(DATA_FILE):
        df = pd.read_csv(DATA_FILE, dtype=str)
        for c in COLUMNS:
            if c not in df.columns:
                df[c] = ""
        return df[COLUMNS]
    return pd.DataFrame(columns=COLUMNS)

def save_data(df):
    df.to_csv(DATA_FILE, index=False)

def generate_patient_id():
    df = load_data()
    if df.empty:
        return "PAT-0001"
    ids = df["patient_id"].dropna().tolist()
    nums = []
    for pid in ids:
        try:
            nums.append(int(pid.split("-")[1]))
        except Exception:
            pass
    next_num = max(nums) + 1 if nums else 1
    return f"PAT-{next_num:04d}"

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
        ["Full Name",        row.get("name",""),       "Date of Birth",    age_str],
        ["Gender",           row.get("gender",""),     "Report Date",      row.get("date","")],
        ["Present Address",  row.get("present_address",""),  "Permanent Address", row.get("permanent_address","")],
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
    ["📋 Add Patient", "🔍 View / Search", "📊 Analytics", "📁 All Reports"],
    label_visibility="collapsed"
)
st.sidebar.markdown("---")
df_all = load_data()
st.sidebar.metric("Total Patients", len(df_all))
if not df_all.empty:
    st.sidebar.metric("Latest Entry", df_all.iloc[-1]["name"] if "name" in df_all.columns else "—")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — ADD PATIENT
# ═══════════════════════════════════════════════════════════════════════════════
if page == "📋 Add Patient":
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
                "gender":            gender,
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

            # Save to CSV
            df_all = load_data()
            df_all = pd.concat([df_all, pd.DataFrame([record])], ignore_index=True)
            save_data(df_all)

            # Generate and save PDF
            pdf_bytes = build_pdf(record)
            pdf_path = os.path.join(PDF_FOLDER, f"{new_id}_{name.replace(' ','_')}.pdf")
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)

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

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — VIEW / SEARCH
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
        df_show[["patient_id","date","name","gender","bmi","hba1c","cholesterol","sleep_score","hrv"]],
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
                st.write("**Gender:**",     row.get("gender"))
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