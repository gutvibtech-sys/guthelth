"""Non-diagnostic skin and color analysis for captured GutVibe face scans.

The functions in this module use only a stored captured face image. They compute
numerical color measurements from the detected face region and persist those
measurements for the selected patient. No disease or medical condition is
inferred or diagnosed.
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
class SkinColorMeasurements:
    """Numerical, non-diagnostic skin and color measurements."""

    overall_skin_tone_l: float
    overall_skin_tone_a: float
    overall_skin_tone_b: float
    overall_skin_rgb_r: float
    overall_skin_rgb_g: float
    overall_skin_rgb_b: float
    skin_uniformity_score: float
    pigmentation_dark_patch_index: float
    dark_patch_area_pct: float
    under_eye_darkness_index: float | None
    facial_redness_index: float
    redness_area_pct: float
    facial_brightness: float
    facial_contrast: float
    analysis_confidence: str


@dataclass(frozen=True)
class SkinColorAnalysis:
    """Complete non-diagnostic result for one captured face image."""

    scan_id: str | None
    face_box: dict[str, int]
    measurements: SkinColorMeasurements
    analysis_note: str = (
        "Non-diagnostic skin and color image measurements only; no disease, "
        "medical condition, or clinical diagnosis is inferred."
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


def _detect_face_box(image) -> tuple[int, int, int, int]:
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    face_cascade = _load_cascade("haarcascade_frontalface_default.xml")
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
    face_box = _largest_box(faces)
    if face_box is None:
        raise ValueError("No face could be detected in the captured image.")
    return face_box


def _central_skin_mask(face_bgr):
    """Return a conservative central-face mask that avoids edges, hair, and mouth."""
    import cv2
    import numpy as np

    h, w = face_bgr.shape[:2]
    y_grid, x_grid = np.ogrid[:h, :w]
    ellipse = (((x_grid - (w / 2)) / (w * 0.36)) ** 2 + ((y_grid - (h * 0.48)) / (h * 0.38)) ** 2) <= 1
    mouth_exclusion = (y_grid > h * 0.68) & (abs(x_grid - (w / 2)) < w * 0.23)

    ycrcb = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2YCrCb)
    _, cr, cb = cv2.split(ycrcb)
    color_skin = (cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127)
    mask = ellipse & ~mouth_exclusion & color_skin

    if int(mask.sum()) < max(100, int(h * w * 0.08)):
        mask = ellipse & ~mouth_exclusion
    return mask.astype(bool)


def _region_mean(l_channel, mask) -> float | None:
    values = l_channel[mask]
    if values.size == 0:
        return None
    return float(values.mean())


def calculate_skin_color_measurements(face_bgr) -> SkinColorMeasurements:
    """Calculate numerical color measurements from a cropped face image."""
    import cv2
    import numpy as np

    face_rgb = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2RGB)
    face_lab = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2LAB)
    l_channel = face_lab[:, :, 0].astype(np.float32) * (100.0 / 255.0)
    a_channel = face_lab[:, :, 1].astype(np.float32) - 128.0
    b_channel = face_lab[:, :, 2].astype(np.float32) - 128.0
    mask = _central_skin_mask(face_bgr)

    l_values = l_channel[mask]
    a_values = a_channel[mask]
    b_values = b_channel[mask]
    rgb_values = face_rgb[mask].astype(np.float32)
    if l_values.size == 0:
        raise ValueError("Unable to isolate facial skin pixels from the captured image.")

    brightness = float(l_values.mean())
    contrast = float(l_values.std())
    uniformity = max(0.0, 100.0 - contrast)
    dark_threshold = max(0.0, brightness - (1.25 * contrast))
    dark_pixels = l_values < dark_threshold

    h, w = face_bgr.shape[:2]
    upper_mask = np.zeros((h, w), dtype=bool)
    upper_mask[int(h * 0.35) : int(h * 0.58), int(w * 0.18) : int(w * 0.82)] = True
    lower_mask = np.zeros((h, w), dtype=bool)
    lower_mask[int(h * 0.58) : int(h * 0.82), int(w * 0.22) : int(w * 0.78)] = True
    under_eye_l = _region_mean(l_channel, mask & upper_mask)
    cheek_l = _region_mean(l_channel, mask & lower_mask)
    under_eye_darkness = None if under_eye_l is None or cheek_l is None else max(0.0, cheek_l - under_eye_l)

    redness_values = a_values - (0.35 * b_values)
    redness_index = float(redness_values.mean())
    redness_threshold = redness_index + max(3.0, float(redness_values.std()))
    redness_area_pct = float((redness_values > redness_threshold).mean() * 100.0)

    confidence = "high" if mask.sum() >= h * w * 0.22 else "medium" if mask.sum() >= h * w * 0.12 else "low"
    return SkinColorMeasurements(
        overall_skin_tone_l=round(brightness, 2),
        overall_skin_tone_a=round(float(a_values.mean()), 2),
        overall_skin_tone_b=round(float(b_values.mean()), 2),
        overall_skin_rgb_r=round(float(rgb_values[:, 0].mean()), 2),
        overall_skin_rgb_g=round(float(rgb_values[:, 1].mean()), 2),
        overall_skin_rgb_b=round(float(rgb_values[:, 2].mean()), 2),
        skin_uniformity_score=round(uniformity, 2),
        pigmentation_dark_patch_index=round(max(0.0, brightness - float(l_values[dark_pixels].mean())) if dark_pixels.any() else 0.0, 2),
        dark_patch_area_pct=round(float(dark_pixels.mean() * 100.0), 2),
        under_eye_darkness_index=None if under_eye_darkness is None else round(float(under_eye_darkness), 2),
        facial_redness_index=round(redness_index, 2),
        redness_area_pct=round(redness_area_pct, 2),
        facial_brightness=round(brightness, 2),
        facial_contrast=round(contrast, 2),
        analysis_confidence=confidence,
    )


def analyze_captured_face_skin_color(image_bytes: bytes, scan_id: str | None = None) -> SkinColorAnalysis:
    """Analyze one captured face image and return non-diagnostic color measurements."""
    import cv2
    import numpy as np

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unable to read the captured face image.")
    x, y, w, h = _detect_face_box(image)
    face_bgr = image[y : y + h, x : x + w]
    measurements = calculate_skin_color_measurements(face_bgr)
    return SkinColorAnalysis(
        scan_id=scan_id,
        face_box={"x": x, "y": y, "width": w, "height": h},
        measurements=measurements,
    )


def get_connection(database_file: str = DATABASE_FILE) -> sqlite3.Connection:
    """Open the patient database and ensure the skin analysis table exists."""
    _secure_database_file(database_file)
    conn = sqlite3.connect(database_file)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skin_color_measurements (
            measurement_id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL,
            scan_id TEXT,
            analyzed_at TEXT NOT NULL,
            face_box_json TEXT NOT NULL,
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
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


def store_skin_color_measurements(patient_id: str, analysis: SkinColorAnalysis, database_file: str = DATABASE_FILE) -> str:
    """Persist non-diagnostic skin and color measurements for a patient."""
    measurement_id = str(uuid.uuid4())
    analyzed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with get_connection(database_file) as conn:
        conn.execute(
            """
            INSERT INTO skin_color_measurements (
                measurement_id, patient_id, scan_id, analyzed_at,
                face_box_json, measurements_json, analysis_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                measurement_id,
                patient_id,
                analysis.scan_id,
                analyzed_at,
                json.dumps(analysis.face_box, sort_keys=True),
                json.dumps(_json_ready(analysis.measurements), sort_keys=True),
                analysis.analysis_note,
            ),
        )
        conn.commit()
    return measurement_id


def analyze_and_store_skin_color(patient_id: str, image_bytes: bytes, scan_id: str | None = None, database_file: str = DATABASE_FILE) -> tuple[str, SkinColorAnalysis]:
    """Analyze a captured face image and store the measurements in SQLite."""
    analysis = analyze_captured_face_skin_color(image_bytes=image_bytes, scan_id=scan_id)
    measurement_id = store_skin_color_measurements(patient_id, analysis, database_file)
    return measurement_id, analysis


def load_latest_skin_color_measurements(patient_id: str, database_file: str = DATABASE_FILE) -> dict[str, Any] | None:
    """Load the latest stored skin and color measurements for a patient."""
    with get_connection(database_file) as conn:
        row = conn.execute(
            """
            SELECT measurement_id, scan_id, analyzed_at, measurements_json, analysis_note
            FROM skin_color_measurements
            WHERE patient_id = ?
            ORDER BY analyzed_at DESC
            LIMIT 1
            """,
            (patient_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "measurement_id": row[0],
        "scan_id": row[1],
        "analyzed_at": row[2],
        "measurements": json.loads(row[3]),
        "analysis_note": row[4],
    }
