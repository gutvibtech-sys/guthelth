import json
import sqlite3

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from skin_color_analysis import (
    SkinColorAnalysis,
    SkinColorMeasurements,
    calculate_skin_color_measurements,
    store_skin_color_measurements,
)


def test_calculate_skin_color_measurements_are_numeric_and_non_diagnostic():
    face = np.full((120, 100, 3), (95, 135, 185), dtype=np.uint8)
    face[42:62, 20:42] = (55, 75, 105)
    face[42:62, 58:80] = (55, 75, 105)
    face[25:55, 65:90] = (80, 90, 200)

    measurements = calculate_skin_color_measurements(face)

    assert 0 <= measurements.overall_skin_tone_l <= 100
    assert measurements.skin_uniformity_score < 100
    assert measurements.dark_patch_area_pct > 0
    assert measurements.under_eye_darkness_index is not None
    assert measurements.facial_redness_index > 0
    assert measurements.facial_brightness == measurements.overall_skin_tone_l
    assert measurements.facial_contrast > 0
    assert measurements.analysis_confidence in {"low", "medium", "high"}


def test_store_skin_color_measurements(tmp_path):
    database = tmp_path / "patients.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE patients (patient_id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE face_scans (scan_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL)")
        conn.execute("INSERT INTO patients VALUES ('P001')")
        conn.execute("INSERT INTO face_scans VALUES ('S001', 'P001')")

    analysis = SkinColorAnalysis(
        scan_id="S001",
        face_box={"x": 1, "y": 2, "width": 100, "height": 120},
        measurements=SkinColorMeasurements(
            overall_skin_tone_l=61.5,
            overall_skin_tone_a=10.2,
            overall_skin_tone_b=21.4,
            overall_skin_rgb_r=185.0,
            overall_skin_rgb_g=135.0,
            overall_skin_rgb_b=95.0,
            skin_uniformity_score=88.1,
            pigmentation_dark_patch_index=12.3,
            dark_patch_area_pct=4.2,
            under_eye_darkness_index=8.4,
            facial_redness_index=2.2,
            redness_area_pct=3.1,
            facial_brightness=61.5,
            facial_contrast=11.9,
            analysis_confidence="high",
        ),
    )

    measurement_id = store_skin_color_measurements("P001", analysis, str(database))

    with sqlite3.connect(database) as conn:
        row = conn.execute(
            "SELECT measurement_id, patient_id, scan_id, face_box_json, measurements_json, analysis_note "
            "FROM skin_color_measurements"
        ).fetchone()

    assert row[0] == measurement_id
    assert row[1] == "P001"
    assert row[2] == "S001"
    assert json.loads(row[3])["width"] == 100
    assert json.loads(row[4])["facial_brightness"] == 61.5
    assert "no disease" in row[5]
