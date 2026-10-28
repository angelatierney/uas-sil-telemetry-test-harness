# UAS SIL Telemetry Test Harness

Production-ready **Software-in-the-Loop (SIL)** flight-test automation and telemetry verification for unmanned aircraft systems. The harness ingests fixed-length binary telemetry frames, evaluates them against a configurable flight envelope and failsafe policy, and emits machine-readable JSON plus a styled HTML execution summary.

---

## Architecture

```
┌─────────────────────┐
│  sample_sil_*.bin   │  36-byte LE telemetry frames
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  TelemetryParser    │  struct.unpack("<dddfff")
└─────────┬───────────┘
          │  List[TelemetryFrame]
          ▼
┌─────────────────────┐
│ SILAssertionEngine  │  loads config/test_criteria.yaml
│  • altitude / speed │
│  • geofence (hav.)  │
│  • low-batt RTL     │
└─────────┬───────────┘
          │  EvaluationResult + AssertionIssue[]
          ▼
┌─────────────────────┐
│ TestReportGenerator │  → test_report.json
│                     │  → test_report.html
└─────────────────────┘
```

| Module | Responsibility |
|--------|----------------|
| `src/telemetry_parser.py` | Unpack / pack 36-byte little-endian SIL frames; reject malformed data |
| `src/assertion_engine.py` | Load YAML criteria; assert altitude, velocity, geofence, and low-battery RTL rules |
| `src/report_generator.py` | Emit `test_report.json` and a styled `test_report.html` summary |
| `main.py` | CLI entry point; mock-log generation; parse → assert → report pipeline |

### Telemetry frame (36 bytes, little-endian)

| Offset | Type   | Field      | Units        |
|-------:|--------|------------|--------------|
| 0      | double | timestamp  | Unix seconds |
| 8      | double | latitude   | deg WGS-84   |
| 16     | double | longitude  | deg WGS-84   |
| 24     | float  | altitude   | m AGL        |
| 28     | float  | velocity   | m/s          |
| 32     | float  | battery    | %            |

---

## Key Features

- **Deterministic binary parsing** with strict frame-size validation and clear `TelemetryParseError` diagnostics
- **YAML-driven flight envelope** — altitude, velocity, geofence radius, and expected failsafe action (`RTL`)
- **Severity-tagged discrepancies** (`HIGH` / `CRITICAL`) with timestamp, measured value, and rule description
- **Low-battery RTL mismatch detection** via range-to-home trend analysis (haversine)
- **Dual-format reporting** — JSON for CI/CD ingestion, HTML for human flight-test review
- **Mock SIL log generator** embedded in `main.py` for offline demos and regression tests

---

## Directory Structure

```
uas-sil-telemetry-test-harness/
├── README.md
├── requirements.txt
├── config/
│   └── test_criteria.yaml          # Flight envelope & failsafe policy
├── data/
│   └── sample_sil_flight_log.bin   # Mock 36-byte telemetry frames
├── src/
│   ├── __init__.py
│   ├── telemetry_parser.py         # TelemetryParser
│   ├── assertion_engine.py         # SILAssertionEngine
│   └── report_generator.py         # TestReportGenerator
├── main.py                         # CLI + sample-log generator
└── tests/
    └── test_flight_harness.py      # PyTest suite
```

---

## Quickstart

### 1. Create a virtual environment (Python 3.11+)

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Generate the sample log (if needed) and run verification

```bash
# First run auto-creates data/sample_sil_flight_log.bin when missing
python main.py

# Force-regenerate the mock log, then evaluate
python main.py --generate-sample
```

### 3. Inspect reports

| Artefact | Description |
|----------|-------------|
| `test_report.json` | Structured pass/fail, frame counts, timestamped issues |
| `test_report.html` | Styled flight-test execution summary (open in a browser) |

### CLI options

```bash
python main.py --help
python main.py --log data/sample_sil_flight_log.bin \
               --criteria config/test_criteria.yaml \
               --output . \
               -v
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--log` | `data/sample_sil_flight_log.bin` | Binary SIL telemetry input |
| `--criteria` | `config/test_criteria.yaml` | Flight-envelope YAML |
| `--output` | `.` (repo root) | Report output directory |
| `--generate-sample` | off | Rewrite the mock binary log |
| `-v` / `--verbose` | off | DEBUG logging |

Exit codes: `0` = PASS, `1` = FAIL (rule violations), `2` = missing inputs, `3` = unexpected error.

---

## Testing

```bash
# From the repository root, with the venv activated:
pytest tests/ -v

# With coverage:
pytest tests/ -v --cov=src --cov-report=term-missing
```

The suite verifies:

1. Frame parsing accuracy (field values & round-trip pack/unpack)
2. Exception handling for short, long, empty, truncated, and non-bytes input
3. Altitude / velocity / geofence breaches and low-battery RTL mismatches
4. JSON + HTML report generation and end-to-end harness execution

---

## Configuration

`config/test_criteria.yaml`:

```yaml
flight_envelope:
  max_altitude_m: 120.0
  max_velocity_ms: 25.0
  low_battery_threshold: 20.0
  expected_failsafe_action: "RTL"

geofence:
  home_latitude_deg: 37.7749
  home_longitude_deg: -122.4194
  radius_m: 500.0
```

Adjust limits to match your SIL airframe and operating area. The sample log intentionally injects altitude, velocity, geofence, and RTL violations so a first-run `FAIL` report demonstrates the full assertion path.

---

## License

Provided as-is for SIL flight-test automation and telemetry verification workflows.
