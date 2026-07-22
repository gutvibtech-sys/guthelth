import json
import sqlite3

from face_landmark_analysis import (
    FaceLandmarks,
    FaceLandmarkAnalysis,
    FaceMeasurements,
    Point,
    _calculate_measurements,
    store_face_landmark_measurements,
)


def test_calculate_measurements_is_non_diagnostic_geometry():
    landmarks = FaceLandmarks(
        left_eye=Point(80, 90),
        right_eye=Point(120, 90),
        nose=Point(100, 125),
        mouth=Point(102, 160),
        jawline=[],
    )

    measurements = _calculate_measurements((50, 50, 100, 150), landmarks)

    assert measurements.symmetry_score == 0.9933
    assert measurements.eye_distance_ratio == 0.4
    assert measurements.nose_center_offset_ratio == 0.0
    assert measurements.mouth_center_offset_ratio == 0.02
    assert measurements.face_width_to_height_ratio == 0.6667
    assert measurements.landmark_confidence == "high"


def test_store_face_landmark_measurements(tmp_path):
    database = tmp_path / "patients.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE patients (patient_id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE face_scans (scan_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL)")
        conn.execute("INSERT INTO patients VALUES ('P001')")
        conn.execute("INSERT INTO face_scans VALUES ('S001', 'P001')")

    analysis = FaceLandmarkAnalysis(
        scan_id="S001",
        face_box={"x": 1, "y": 2, "width": 100, "height": 120},
        landmarks=FaceLandmarks(
            left_eye=Point(30, 40),
            right_eye=Point(70, 40),
            nose=Point(50, 60),
            mouth=Point(50, 90),
            jawline=[Point(20, 110), Point(50, 120), Point(80, 110)],
        ),
        measurements=FaceMeasurements(
            symmetry_score=1.0,
            eye_distance_ratio=0.4,
            nose_center_offset_ratio=0.0,
            mouth_center_offset_ratio=0.0,
            face_width_to_height_ratio=0.8333,
            eye_to_mouth_height_ratio=0.4167,
            nose_to_mouth_height_ratio=0.25,
            landmark_confidence="high",
        ),
    )

    measurement_id = store_face_landmark_measurements("P001", analysis, str(database))

    with sqlite3.connect(database) as conn:
        row = conn.execute(
            "SELECT measurement_id, patient_id, scan_id, landmarks_json, measurements_json, analysis_note "
            "FROM face_landmark_measurements"
        ).fetchone()

    assert row[0] == measurement_id
    assert row[1] == "P001"
    assert row[2] == "S001"
    assert json.loads(row[3])["nose"] == {"x": 50, "y": 60}
    assert json.loads(row[4])["symmetry_score"] == 1.0
    assert "no medical diagnosis" in row[5]
