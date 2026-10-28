"""
PyTest suite for the UAS SIL Telemetry Test Harness.

Covers:
  * accurate unpacking of 36-byte little-endian frames
  * exception handling for malformed / truncated data
  * altitude (and related) envelope breach detection
  * report generation (JSON + HTML artefacts)
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
import yaml

from src.assertion_engine import EvaluationResult, SILAssertionEngine
from src.report_generator import TestReportGenerator
from src.telemetry_parser import (
    FRAME_FORMAT,
    FRAME_SIZE,
    TelemetryFrame,
    TelemetryParseError,
    TelemetryParser,
)
from main import generate_sample_log, run_harness

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
CRITERIA_PATH = ROOT / "config" / "test_criteria.yaml"


@pytest.fixture
def parser() -> TelemetryParser:
    return TelemetryParser()


@pytest.fixture
def engine() -> SILAssertionEngine:
    return SILAssertionEngine(CRITERIA_PATH)


@pytest.fixture
def sample_frame_bytes() -> bytes:
    """A single well-formed nominal frame."""
    return TelemetryParser.pack_frame(
        timestamp=1_700_000_000.0,
        latitude=37.7749,
        longitude=-122.4194,
        altitude=50.0,
        velocity=10.0,
        battery=85.0,
    )


@pytest.fixture
def tmp_criteria(tmp_path: Path) -> Path:
    """Minimal criteria file for isolated unit tests."""
    data = {
        "flight_envelope": {
            "max_altitude_m": 120.0,
            "max_velocity_ms": 25.0,
            "low_battery_threshold": 20.0,
            "expected_failsafe_action": "RTL",
        },
        "geofence": {
            "home_latitude_deg": 37.7749,
            "home_longitude_deg": -122.4194,
            "radius_m": 500.0,
        },
    }
    path = tmp_path / "test_criteria.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


# ===========================================================================
# Frame parsing
# ===========================================================================

class TestTelemetryParser:
    def test_frame_size_constant(self):
        assert FRAME_SIZE == 36
        assert struct.calcsize(FRAME_FORMAT) == 36

    def test_parse_frame_accuracy(
        self, parser: TelemetryParser, sample_frame_bytes: bytes
    ):
        frame = parser.parse_frame(sample_frame_bytes)

        assert isinstance(frame, TelemetryFrame)
        assert frame.timestamp == pytest.approx(1_700_000_000.0)
        assert frame.latitude == pytest.approx(37.7749)
        assert frame.longitude == pytest.approx(-122.4194)
        assert frame.altitude == pytest.approx(50.0)
        assert frame.velocity == pytest.approx(10.0)
        assert frame.battery == pytest.approx(85.0)

    def test_pack_roundtrip(self, parser: TelemetryParser):
        original = (1_700_000_001.5, 37.78, -122.42, 99.5, 12.25, 67.0)
        packed = TelemetryParser.pack_frame(*original)
        assert len(packed) == 36
        frame = parser.parse_frame(packed)
        assert (
            frame.timestamp,
            frame.latitude,
            frame.longitude,
            frame.altitude,
            frame.velocity,
            frame.battery,
        ) == pytest.approx(original)

    def test_parse_file(self, parser: TelemetryParser, tmp_path: Path):
        log = tmp_path / "mini.bin"
        payload = b"".join(
            TelemetryParser.pack_frame(
                1_700_000_000.0 + i, 37.7749, -122.4194, 40.0, 5.0, 90.0
            )
            for i in range(5)
        )
        log.write_bytes(payload)

        frames = parser.parse_file(log)
        assert len(frames) == 5
        assert frames[4].timestamp == pytest.approx(1_700_000_004.0)

    def test_to_dict(self, parser: TelemetryParser, sample_frame_bytes: bytes):
        d = parser.parse_frame(sample_frame_bytes).to_dict()
        assert set(d.keys()) == {
            "timestamp", "latitude", "longitude",
            "altitude", "velocity", "battery",
        }


# ===========================================================================
# Exception handling for bad data
# ===========================================================================

class TestParseErrorHandling:
    def test_short_frame_raises(self, parser: TelemetryParser):
        with pytest.raises(TelemetryParseError, match="Invalid frame size"):
            parser.parse_frame(b"\x00" * 20)

    def test_long_frame_raises(self, parser: TelemetryParser):
        with pytest.raises(TelemetryParseError, match="Invalid frame size"):
            parser.parse_frame(b"\x00" * 40)

    def test_empty_frame_raises(self, parser: TelemetryParser):
        with pytest.raises(TelemetryParseError, match="Invalid frame size"):
            parser.parse_frame(b"")

    def test_non_bytes_raises(self, parser: TelemetryParser):
        with pytest.raises(TelemetryParseError, match="bytes-like"):
            parser.parse_frame("not-bytes")  # type: ignore[arg-type]

    def test_truncated_file_raises(self, parser: TelemetryParser, tmp_path: Path):
        bad = tmp_path / "truncated.bin"
        bad.write_bytes(b"\x00" * 50)  # 50 % 36 != 0
        with pytest.raises(TelemetryParseError, match="not an integer multiple"):
            parser.parse_file(bad)

    def test_missing_file_raises(self, parser: TelemetryParser, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            parser.parse_file(tmp_path / "does_not_exist.bin")

    def test_truncated_stream_raises(self, parser: TelemetryParser, tmp_path: Path):
        """Stream that ends mid-frame after one complete frame."""
        log = tmp_path / "partial.bin"
        good = TelemetryParser.pack_frame(
            1.0, 37.0, -122.0, 10.0, 1.0, 50.0
        )
        log.write_bytes(good + b"\x00" * 10)

        # Bypass the file-size multiple check by using the stream API directly.
        with log.open("rb") as fh, pytest.raises(
            TelemetryParseError, match="Truncated frame"
        ):
            list(parser.parse_stream(fh))


# ===========================================================================
# Assertion engine — altitude & envelope breaches
# ===========================================================================

class TestAssertionEngine:
    def _frame(self, **overrides) -> TelemetryFrame:
        base = dict(
            timestamp=1_700_000_000.0,
            latitude=37.7749,
            longitude=-122.4194,
            altitude=50.0,
            velocity=10.0,
            battery=85.0,
        )
        base.update(overrides)
        return TelemetryFrame(**base)

    def test_nominal_frame_passes(self, engine: SILAssertionEngine):
        issues = engine.evaluate_frame(self._frame())
        assert issues == []

    def test_altitude_breach_critical(self, engine: SILAssertionEngine):
        issues = engine.evaluate_frame(self._frame(altitude=150.0))
        assert len(issues) == 1
        assert issues[0].rule == "MAX_ALTITUDE"
        assert issues[0].severity == "CRITICAL"
        assert issues[0].measured_value == pytest.approx(150.0)
        assert issues[0].limit_value == pytest.approx(120.0)

    def test_velocity_breach_high(self, engine: SILAssertionEngine):
        issues = engine.evaluate_frame(self._frame(velocity=30.0))
        assert len(issues) == 1
        assert issues[0].rule == "MAX_VELOCITY"
        assert issues[0].severity == "HIGH"

    def test_geofence_breach(self, engine: SILAssertionEngine):
        # ~700 m north of home (> 500 m radius)
        lat = 37.7749 + 700.0 / 111_320.0
        issues = engine.evaluate_frame(self._frame(latitude=lat))
        assert any(i.rule == "GEOFENCE" and i.severity == "CRITICAL" for i in issues)

    def test_low_battery_rtl_mismatch(self, engine: SILAssertionEngine):
        # First frame establishes range baseline.
        engine.evaluate_frame(
            self._frame(latitude=37.7749 + 100.0 / 111_320.0, battery=15.0)
        )
        # Second frame moves further outbound with critically low battery.
        issues = engine.evaluate_frame(
            self._frame(latitude=37.7749 + 200.0 / 111_320.0, battery=12.0, altitude=40.0, velocity=8.0)
        )
        assert any(i.rule == "LOW_BATTERY_RTL" for i in issues)
        assert all(
            i.severity == "CRITICAL"
            for i in issues
            if i.rule == "LOW_BATTERY_RTL"
        )

    def test_evaluate_sequence_fail(self, engine: SILAssertionEngine):
        frames = [
            self._frame(altitude=50.0),
            self._frame(timestamp=1_700_000_001.0, altitude=130.0),
        ]
        result = engine.evaluate(frames)
        assert result.frames_evaluated == 2
        assert result.passed is False
        assert len(result.issues) >= 1

    def test_evaluate_sequence_pass(self, engine: SILAssertionEngine):
        frames = [self._frame(timestamp=1_700_000_000.0 + i) for i in range(3)]
        result = engine.evaluate(frames)
        assert result.passed is True
        assert result.issues == []

    def test_missing_criteria_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            SILAssertionEngine(tmp_path / "missing.yaml")

    def test_criteria_values_loaded(self, tmp_criteria: Path):
        eng = SILAssertionEngine(tmp_criteria)
        assert eng.max_altitude_m == 120.0
        assert eng.max_velocity_ms == 25.0
        assert eng.low_battery_threshold == 20.0
        assert eng.expected_failsafe_action == "RTL"


# ===========================================================================
# Report generation
# ===========================================================================

class TestReportGeneration:
    def _result_with_issue(self) -> EvaluationResult:
        from src.assertion_engine import AssertionIssue

        result = EvaluationResult(frames_evaluated=10, passed=False)
        result.issues.append(
            AssertionIssue(
                timestamp=1_700_000_005.0,
                severity="CRITICAL",
                rule="MAX_ALTITUDE",
                description="Altitude breach: 130.0 m exceeds envelope limit of 120.0 m.",
                measured_value=130.0,
                limit_value=120.0,
            )
        )
        return result

    def test_json_report_structure(self, tmp_path: Path):
        gen = TestReportGenerator(output_dir=tmp_path)
        paths = gen.generate(self._result_with_issue(), metadata={"run": "unit"})

        assert paths["json"].is_file()
        data = json.loads(paths["json"].read_text(encoding="utf-8"))

        assert data["status"] == "FAIL"
        assert data["frames_evaluated"] == 10
        assert data["issue_count"] == 1
        assert data["severity_summary"]["CRITICAL"] == 1
        assert data["metadata"]["run"] == "unit"
        assert data["issues"][0]["rule"] == "MAX_ALTITUDE"

    def test_html_report_contains_status(self, tmp_path: Path):
        gen = TestReportGenerator(output_dir=tmp_path)
        paths = gen.generate(self._result_with_issue())

        html = paths["html"].read_text(encoding="utf-8")
        assert paths["html"].is_file()
        assert "FAIL" in html
        assert "MAX_ALTITUDE" in html
        assert "Frames Evaluated" in html
        assert "130.00" in html

    def test_pass_report(self, tmp_path: Path):
        gen = TestReportGenerator(output_dir=tmp_path)
        result = EvaluationResult(frames_evaluated=5, passed=True)
        paths = gen.generate(result)

        data = json.loads(paths["json"].read_text(encoding="utf-8"))
        assert data["status"] == "PASS"
        assert data["issue_count"] == 0

        html = paths["html"].read_text(encoding="utf-8")
        assert "PASS" in html
        assert "No issues detected" in html


# ===========================================================================
# End-to-end: sample log + full pipeline
# ===========================================================================

class TestEndToEnd:
    def test_generate_sample_log(self, tmp_path: Path):
        log = tmp_path / "sample_sil_flight_log.bin"
        generate_sample_log(log, num_frames=20)

        assert log.is_file()
        assert log.stat().st_size == 20 * FRAME_SIZE

        frames = TelemetryParser().parse_file(log)
        assert len(frames) == 20

    def test_full_harness_detects_breaches(self, tmp_path: Path):
        log = tmp_path / "sample_sil_flight_log.bin"
        generate_sample_log(log)

        exit_code = run_harness(log, CRITERIA_PATH, tmp_path)
        assert exit_code == 1  # intentional violations → FAIL

        report = json.loads((tmp_path / "test_report.json").read_text())
        assert report["status"] == "FAIL"
        assert report["frames_evaluated"] == 20
        assert report["issue_count"] > 0

        rules = {i["rule"] for i in report["issues"]}
        assert "MAX_ALTITUDE" in rules
        assert (tmp_path / "test_report.html").is_file()
