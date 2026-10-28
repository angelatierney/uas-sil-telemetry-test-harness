#!/usr/bin/env python3
"""
UAS SIL Telemetry Test Harness — CLI entry point.

Reads a binary SIL flight log, parses fixed 36-byte telemetry frames,
evaluates them against ``config/test_criteria.yaml``, and exports
JSON / HTML flight-test reports.

Usage
-----
    # Generate mock log (if missing) and run full verification:
    python main.py

    # Force-regenerate the sample binary log, then verify:
    python main.py --generate-sample

    # Point at a custom log / criteria / output directory:
    python main.py --log data/sample_sil_flight_log.bin \\
                   --criteria config/test_criteria.yaml \\
                   --output .
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.assertion_engine import SILAssertionEngine
from src.report_generator import TestReportGenerator
from src.telemetry_parser import TelemetryParser

# ---------------------------------------------------------------------------
# Paths relative to this script
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DEFAULT_LOG = ROOT / "data" / "sample_sil_flight_log.bin"
DEFAULT_CRITERIA = ROOT / "config" / "test_criteria.yaml"
DEFAULT_OUTPUT = ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sil_harness")


# ===========================================================================
# Mock SIL log generation
# ===========================================================================

def generate_sample_log(path: Path, num_frames: int = 20) -> Path:
    """
    Write a deterministic mock SIL binary log containing intentional
    envelope / geofence / low-battery violations for demonstration.

    Frame timeline (home ≈ 37.7749, -122.4194):
      0–4   Nominal climb / cruise inside envelope          → PASS
      5–7   Altitude exceeds 120 m                          → CRITICAL
      8–10  Velocity exceeds 25 m/s                         → HIGH
      11–13 Geofence excursion (>500 m from home)           → CRITICAL
      14–16 Low battery while flying outbound (no RTL)      → CRITICAL
      17–19 Nominal RTL descent with recovering battery     → PASS
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    home_lat, home_lon = 37.7749, -122.4194
    # ~1° latitude ≈ 111 320 m; use for crude offset injection.
    meters_to_deg_lat = 1.0 / 111_320.0

    frames_bytes = bytearray()
    t0 = 1_700_000_000.0  # fixed epoch for reproducibility

    for i in range(num_frames):
        t = t0 + i * 1.0  # 1 Hz sample rate

        if i < 5:
            # Nominal climb
            lat = home_lat + i * 10 * meters_to_deg_lat
            lon = home_lon
            alt = 30.0 + i * 10.0          # 30 → 70 m
            vel = 8.0 + i * 0.5
            batt = 90.0 - i * 1.0

        elif i < 8:
            # Altitude breach
            lat = home_lat + 50 * meters_to_deg_lat
            lon = home_lon
            alt = 125.0 + (i - 5) * 5.0    # 125, 130, 135 m
            vel = 12.0
            batt = 80.0

        elif i < 11:
            # Velocity breach
            lat = home_lat + 80 * meters_to_deg_lat
            lon = home_lon
            alt = 60.0
            vel = 28.0 + (i - 8) * 2.0     # 28, 30, 32 m/s
            batt = 70.0

        elif i < 14:
            # Geofence breach — ~600–700 m north of home
            offset_m = 600.0 + (i - 11) * 50.0
            lat = home_lat + offset_m * meters_to_deg_lat
            lon = home_lon
            alt = 50.0
            vel = 10.0
            batt = 55.0

        elif i < 17:
            # Low battery, continuing outbound (RTL mismatch)
            offset_m = 200.0 + (i - 14) * 40.0  # increasing range
            lat = home_lat + offset_m * meters_to_deg_lat
            lon = home_lon
            alt = 40.0
            vel = 8.0
            batt = 15.0 - (i - 14) * 2.0   # 15, 13, 11 %

        else:
            # Nominal RTL — closing range, descending
            remaining = 19 - i             # 2, 1, 0
            offset_m = 30.0 * remaining
            lat = home_lat + offset_m * meters_to_deg_lat
            lon = home_lon
            alt = 10.0 * remaining + 5.0
            vel = 4.0
            batt = 40.0                    # recovered / swapped in SIL

        frames_bytes.extend(
            TelemetryParser.pack_frame(t, lat, lon, alt, vel, batt)
        )

    path.write_bytes(frames_bytes)
    logger.info(
        "Wrote %d mock frames (%d bytes) → %s",
        num_frames,
        len(frames_bytes),
        path,
    )
    return path


# ===========================================================================
# Pipeline
# ===========================================================================

def run_harness(
    log_path: Path,
    criteria_path: Path,
    output_dir: Path,
) -> int:
    """
    Execute the full parse → assert → report pipeline.

    Returns process exit code: 0 on PASS, 1 on FAIL.
    """
    logger.info("=== UAS SIL Telemetry Test Harness ===")
    logger.info("Log      : %s", log_path)
    logger.info("Criteria : %s", criteria_path)
    logger.info("Output   : %s", output_dir)

    # 1. Parse
    parser = TelemetryParser()
    frames = parser.parse_file(log_path)
    logger.info("Parsed %d telemetry frames", len(frames))

    # 2. Assert
    engine = SILAssertionEngine(criteria_path)
    result = engine.evaluate(frames)

    # 3. Report
    reporter = TestReportGenerator(output_dir=output_dir)
    paths = reporter.generate(
        result,
        metadata={
            "log_file": str(log_path),
            "criteria_file": str(criteria_path),
            "frame_size_bytes": TelemetryParser().frame_size,
            "sample_rate_hz": "1.0 (mock)",
        },
    )

    status = "PASS" if result.passed else "FAIL"
    logger.info(
        "Result: %s  |  frames=%d  issues=%d",
        status,
        result.frames_evaluated,
        len(result.issues),
    )
    logger.info("JSON report : %s", paths["json"])
    logger.info("HTML report : %s", paths["html"])

    return 0 if result.passed else 1


# ===========================================================================
# CLI
# ===========================================================================

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "UAS SIL Telemetry Test Harness — parse binary flight logs, "
            "evaluate envelope / failsafe criteria, and export reports."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG,
        help="Path to binary SIL telemetry log (.bin)",
    )
    ap.add_argument(
        "--criteria",
        type=Path,
        default=DEFAULT_CRITERIA,
        help="Path to YAML test-criteria file",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory for JSON / HTML report output",
    )
    ap.add_argument(
        "--generate-sample",
        action="store_true",
        help="(Re)generate the mock sample SIL flight log before running",
    )
    ap.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Ensure a sample log exists (or force-regenerate on request).
    if args.generate_sample or not args.log.is_file():
        if args.log == DEFAULT_LOG or args.generate_sample:
            generate_sample_log(args.log)
        elif not args.log.is_file():
            logger.error("Telemetry log not found: %s", args.log)
            return 2

    try:
        return run_harness(args.log, args.criteria, args.output)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — top-level CLI guard
        logger.exception("Harness failed: %s", exc)
        return 3


if __name__ == "__main__":
    sys.exit(main())
