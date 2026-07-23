import sqlite3

import pytest

from hardware_manager import (
    CalibrationResult, DeviceHealth, DeviceState, DiagnosticResult, FaceDetection,
    HardwareError, HardwareManager, HardwareStore, Measurement,
    PaymentNotEnabledError, PhotoCapture, QualityResult,
)


class HealthyDevice:
    def health(self):
        return DeviceHealth(DeviceState.ONLINE, "ready", 88)

    def restart(self):
        return True

    def diagnose(self):
        return DiagnosticResult(True, "self-test passed")


class Camera(HealthyDevice):
    def __init__(self, faces=1, acceptable=True):
        self.faces, self.acceptable = faces, acceptable

    def capture_photo(self):
        return PhotoCapture(b"photo")

    def detect_faces(self, photo):
        return FaceDetection(self.faces, 0.98)

    def check_quality(self, photo):
        return QualityResult(self.acceptable, 0.92)


class Height(HealthyDevice):
    def read_height(self):
        return Measurement(172.4, "cm")

    def calibrate(self, reference_height, unit="cm"):
        return CalibrationResult(True, reference_height, unit, "height calibrated")


class Scale(HealthyDevice):
    def __init__(self, stable=True):
        self.stable = stable

    def read_weight(self):
        return Measurement(68.2, "kg", self.stable)

    def wait_for_stable_weight(self, timeout_seconds=10):
        return self.read_weight()

    def calibrate(self, reference_weight, unit="kg"):
        return CalibrationResult(True, reference_weight, unit, "scale calibrated")


@pytest.fixture
def manager(tmp_path):
    return HardwareManager(HardwareStore(tmp_path / "hardware.db"))


def test_schema_creates_all_hardware_tables(manager):
    with sqlite3.connect(manager.store.database) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"hardware_devices", "hardware_logs", "calibration_history", "device_health"} <= tables


def test_provider_registration_is_typed_and_persisted(manager):
    manager.register("camera", Camera(), vendor="replaceable", model="test")
    assert manager.provider("camera").health().status is DeviceState.ONLINE
    assert manager.store.rows("hardware_devices")[0]["vendor"] == "replaceable"
    with pytest.raises(TypeError):
        manager.register("camera", HealthyDevice())
    with pytest.raises(ValueError):
        manager.register("unsupported", HealthyDevice())


def test_camera_capture_validates_face_and_quality(manager):
    manager.register("camera", Camera())
    photo, face, quality = manager.capture_patient_photo()
    assert photo.image == b"photo" and face.face_count == 1 and quality.acceptable

    manager.register("camera", Camera(faces=2))
    with pytest.raises(HardwareError, match="exactly one"):
        manager.capture_patient_photo()


def test_measurements_stability_and_calibration_audit(manager):
    manager.register("height_sensor", Height())
    manager.register("weight_scale", Scale())
    assert manager.read_height().value == 172.4
    assert manager.read_stable_weight().value == 68.2
    result = manager.calibrate("weight_scale", 50, "kg")
    assert result.successful
    assert manager.store.rows("calibration_history")[0]["reference_value"] == 50

    manager.register("weight_scale", Scale(stable=False))
    with pytest.raises(HardwareError, match="did not stabilize"):
        manager.read_stable_weight()


def test_health_diagnostics_restart_and_error_logging(manager):
    manager.register("camera", Camera())
    health = manager.health_check()
    assert health["camera"].battery_percent == 88
    assert manager.diagnose("camera").successful
    assert manager.restart("camera") is True
    assert manager.store.rows("device_health")[0]["status"] == "online"
    assert {row["operation"] for row in manager.store.rows("hardware_logs")} >= {
        "register", "diagnose", "restart"
    }


def test_provider_errors_are_normalized_and_missing_provider_is_clear(manager):
    with pytest.raises(HardwareError, match="no provider"):
        manager.read_height()
    with pytest.raises(ValueError, match="Only height"):
        manager.calibrate("camera", 1, "x")
    with pytest.raises(ValueError, match="Unsupported"):
        manager.store.rows("patients")


def test_real_payments_are_explicitly_disabled(manager):
    with pytest.raises(PaymentNotEnabledError, match="not enabled"):
        manager.create_payment_intent(100, "INR")
