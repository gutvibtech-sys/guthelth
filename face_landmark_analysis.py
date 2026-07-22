"""Face landmark analysis utilities for captured patient face scans.

This module performs non-diagnostic geometry analysis on a single captured face
image. It detects visible facial landmarks with OpenCV Haar cascades where
available, estimates jawline points from the detected face bounding box, computes
symmetry/proportion measurements, and persists the resulting measurements to the
patient SQLite database.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


DATABASE_FILE = "gutvibe_patients.db"


@dataclass(frozen=True)
class Point:
    """A two-dimensional image point in pixel coordinates."""

    x: float
    y: float


@dataclass(frozen=True)
class FaceLandmarks:
    """Detected and estimated facial landmark locations."""

    left_eye: Point | None
    right_eye: Point | None
    nose: Point | None
    mouth: Point | None
    jawline: list[Point]


@dataclass(frozen=True)
class FaceMeasurements:
    """Non-diagnostic facial symmetry and proportion measurements."""

    symmetry_score: float
    eye_distance_ratio: float | None
    nose_center_offset_ratio: float | None
    mouth_center_offset_ratio: float | None
    face_width_to_height_ratio: float
    eye_to_mouth_height_ratio: float | None
    nose_to_mouth_height_ratio: float | None
    landmark_confidence: str


@dataclass(frozen=True)
class FaceLandmarkAnalysis:
    """Complete non-diagnostic result for one captured face image."""

    scan_id: str | None
    face_box: dict[str, int]
    landmarks: FaceLandmarks
    measurements: FaceMeasurements
    analysis_note: str = (
        "Non-diagnostic facial landmark geometry only; no medical diagnosis or "
        "clinical interpretation is made."
    )


def _secure_database_file(database_file: str) -> None:
    if os.path.exists(database_file):
        os.chmod(database_file, 0o600)
        return
    open(database_file, "a", encoding="utf-8").close()
    os.chmod(database_file, 0o600)


def _load_cascade(filename: str):
    import cv2

    cascade_path = os.path.join(cv2.data.haarcascades, filename)
    cascade = cv2.CascadeClassifier(cascade_path)
    if cascade.empty():
        raise RuntimeError(f"OpenCV cascade could not be loaded: {filename}")
    return cascade


def _largest_box(boxes) -> tuple[int, int, int, int] | None:
    if boxes is None or len(boxes) == 0:
        return None
    return tuple(int(v) for v in max(boxes, key=lambda box: int(box[2]) * int(box[3])))


def _point_from_box(box: tuple[int, int, int, int], x_offset: int = 0, y_offset: int = 0) -> Point:
    x, y, w, h = box
    return Point(x=x_offset + x + (w / 2), y=y_offset + y + (h / 2))


def _detect_eye_points(face_gray, face_x: int, face_y: int) -> tuple[Point | None, Point | None]:
    eye_cascade = _load_cascade("haarcascade_eye.xml")
    eyes = eye_cascade.detectMultiScale(face_gray, scaleFactor=1.08, minNeighbors=5, minSize=(15, 15))
    if len(eyes) < 2:
        return None, None

    upper_half_limit = face_gray.shape[0] * 0.65
    eye_boxes = [tuple(int(v) for v in eye) for eye in eyes if eye[1] + eye[3] / 2 <= upper_half_limit]
    if len(eye_boxes) < 2:
        eye_boxes = [tuple(int(v) for v in eye) for eye in eyes]

    eye_boxes = sorted(eye_boxes, key=lambda box: box[2] * box[3], reverse=True)[:2]
    eye_points = sorted((_point_from_box(box, face_x, face_y) for box in eye_boxes), key=lambda p: p.x)
    return eye_points[0], eye_points[1]


def _detect_optional_feature(
    face_gray,
    face_x: int,
    face_y: int,
    cascade_name: str,
    region: tuple[float, float],
) -> Point | None:
    try:
        cascade = _load_cascade(cascade_name)
    except RuntimeError:
        return None
    start_ratio, end_ratio = region
    start_y = int(face_gray.shape[0] * start_ratio)
    end_y = int(face_gray.shape[0] * end_ratio)
    roi = face_gray[start_y:end_y, :]
    boxes = cascade.detectMultiScale(roi, scaleFactor=1.08, minNeighbors=5, minSize=(20, 20))
    box = _largest_box(boxes)
    if box is None:
        return None
    return _point_from_box(box, face_x, face_y + start_y)


def _estimate_jawline(face_box: tuple[int, int, int, int]) -> list[Point]:
    x, y, w, h = face_box
    return [
        Point(x + w * 0.10, y + h * 0.72),
        Point(x + w * 0.25, y + h * 0.90),
        Point(x + w * 0.50, y + h),
        Point(x + w * 0.75, y + h * 0.90),
        Point(x + w * 0.90, y + h * 0.72),
    ]


def _distance(a: Point, b: Point) -> float:
    return float(((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5)


def _offset_ratio(point: Point | None, center_x: float, face_width: float) -> float | None:
    if point is None or face_width == 0:
        return None
    return round(abs(point.x - center_x) / face_width, 4)


def _calculate_measurements(
    face_box: tuple[int, int, int, int], landmarks: FaceLandmarks
) -> FaceMeasurements:
    x, y, w, h = face_box
    center_x = x + (w / 2)
    eye_distance_ratio = None
    eye_to_mouth_height_ratio = None
    if landmarks.left_eye and landmarks.right_eye:
        eye_distance_ratio = round(_distance(landmarks.left_eye, landmarks.right_eye) / w, 4)
        if landmarks.mouth:
            eye_mid_y = (landmarks.left_eye.y + landmarks.right_eye.y) / 2
            eye_to_mouth_height_ratio = round(abs(landmarks.mouth.y - eye_mid_y) / h, 4)

    nose_offset = _offset_ratio(landmarks.nose, center_x, w)
    mouth_offset = _offset_ratio(landmarks.mouth, center_x, w)
    nose_to_mouth_height_ratio = None
    if landmarks.nose and landmarks.mouth:
        nose_to_mouth_height_ratio = round(abs(landmarks.mouth.y - landmarks.nose.y) / h, 4)

    offsets = [value for value in (nose_offset, mouth_offset) if value is not None]
    if landmarks.left_eye and landmarks.right_eye:
        left_eye_offset = abs(center_x - landmarks.left_eye.x)
        right_eye_offset = abs(landmarks.right_eye.x - center_x)
        offsets.append(abs(left_eye_offset - right_eye_offset) / w)
    symmetry_score = round(max(0.0, 1.0 - (sum(offsets) / len(offsets) if offsets else 0.0)), 4)

    detected_count = sum(
        landmark is not None
        for landmark in (landmarks.left_eye, landmarks.right_eye, landmarks.nose, landmarks.mouth)
    )
    confidence = "high" if detected_count == 4 else "medium" if detected_count >= 2 else "low"

    return FaceMeasurements(
        symmetry_score=symmetry_score,
        eye_distance_ratio=eye_distance_ratio,
        nose_center_offset_ratio=nose_offset,
        mouth_center_offset_ratio=mouth_offset,
        face_width_to_height_ratio=round(w / h, 4),
        eye_to_mouth_height_ratio=eye_to_mouth_height_ratio,
        nose_to_mouth_height_ratio=nose_to_mouth_height_ratio,
        landmark_confidence=confidence,
    )


def analyze_captured_face_image(image_bytes: bytes, scan_id: str | None = None) -> FaceLandmarkAnalysis:
    """Analyze one captured face image and return non-diagnostic measurements."""
    import cv2
    import numpy as np

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unable to read the captured face image.")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    face_cascade = _load_cascade("haarcascade_frontalface_default.xml")
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
    face_box = _largest_box(faces)
    if face_box is None:
        raise ValueError("No face could be detected in the captured image.")

    x, y, w, h = face_box
    face_gray = gray[y : y + h, x : x + w]
    left_eye, right_eye = _detect_eye_points(face_gray, x, y)
    nose = _detect_optional_feature(face_gray, x, y, "haarcascade_mcs_nose.xml", (0.25, 0.75))
    mouth = _detect_optional_feature(face_gray, x, y, "haarcascade_smile.xml", (0.50, 1.0))
    landmarks = FaceLandmarks(
        left_eye=left_eye,
        right_eye=right_eye,
        nose=nose,
        mouth=mouth,
        jawline=_estimate_jawline(face_box),
    )
    measurements = _calculate_measurements(face_box, landmarks)
    return FaceLandmarkAnalysis(
        scan_id=scan_id,
        face_box={"x": x, "y": y, "width": w, "height": h},
        landmarks=landmarks,
        measurements=measurements,
    )


def get_connection(database_file: str = DATABASE_FILE) -> sqlite3.Connection:
    """Open the patient database and ensure the measurement table exists."""
    _secure_database_file(database_file)
    conn = sqlite3.connect(database_file)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS face_landmark_measurements (
            measurement_id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL,
            scan_id TEXT,
            analyzed_at TEXT NOT NULL,
            landmarks_json TEXT NOT NULL,
            measurements_json TEXT NOT NULL,
            analysis_note TEXT NOT NULL,
            FOREIGN KEY(patient_id) REFERENCES patients(patient_id) ON DELETE CASCADE,
            FOREIGN KEY(scan_id) REFERENCES face_scans(scan_id) ON DELETE SET NULL
        )
        """
    )
    conn.commit()
    return conn


def _json_ready(value: Any) -> Any:
    if isinstance(value, Point):
        return asdict(value)
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_ready(item) for key, item in asdict(value).items()}
    return value


def store_face_landmark_measurements(
    patient_id: str,
    analysis: FaceLandmarkAnalysis,
    database_file: str = DATABASE_FILE,
) -> str:
    """Persist non-diagnostic face landmark measurements for a patient."""
    measurement_id = str(uuid.uuid4())
    analyzed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with get_connection(database_file) as conn:
        conn.execute(
            """
            INSERT INTO face_landmark_measurements (
                measurement_id, patient_id, scan_id, analyzed_at,
                landmarks_json, measurements_json, analysis_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                measurement_id,
                patient_id,
                analysis.scan_id,
                analyzed_at,
                json.dumps(_json_ready(analysis.landmarks), sort_keys=True),
                json.dumps(_json_ready(analysis.measurements), sort_keys=True),
                analysis.analysis_note,
            ),
        )
        conn.commit()
    return measurement_id


def analyze_and_store_captured_face_image(
    patient_id: str,
    image_bytes: bytes,
    scan_id: str | None = None,
    database_file: str = DATABASE_FILE,
) -> tuple[str, FaceLandmarkAnalysis]:
    """Analyze a captured face image and store the measurements in SQLite."""
    analysis = analyze_captured_face_image(image_bytes=image_bytes, scan_id=scan_id)
    measurement_id = store_face_landmark_measurements(patient_id, analysis, database_file)
    return measurement_id, analysis
