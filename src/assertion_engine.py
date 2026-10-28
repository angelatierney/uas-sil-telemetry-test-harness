"""
SIL assertion engine — evaluates parsed telemetry against flight-envelope
and failsafe criteria defined in ``config/test_criteria.yaml``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from .telemetry_parser import TelemetryFrame

logger = logging.getLogger(__name__)

# Mean Earth radius used for haversine distance (meters).
_EARTH_RADIUS_M = 6_371_000.0


@dataclass
class AssertionIssue:
    """A single rule violation discovered during SIL evaluation."""

    timestamp: float
    severity: str          # "HIGH" | "CRITICAL"
    rule: str
    description: str
    measured_value: Optional[float] = None
    limit_value: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvaluationResult:
    """Aggregate outcome of evaluating a full telemetry sequence."""

    frames_evaluated: int = 0
    passed: bool = True
    issues: List[AssertionIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "frames_evaluated": self.frames_evaluated,
            "passed": self.passed,
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
        }


class SILAssertionEngine:
    """
    Load flight-test criteria and assert telemetry frames against them.

    Checks performed per frame
    --------------------------
    * Altitude envelope breach  (``max_altitude_m``)          → CRITICAL
    * Velocity envelope breach  (``max_velocity_ms``)         → HIGH
    * Circular geofence breach  (``geofence.radius_m``)       → CRITICAL
    * Low-battery failsafe: when battery ≤ threshold the
      vehicle must be closing range to home (RTL behaviour).
      Persistent outbound motion while critically low on
      battery is flagged as an RTL mode mismatch            → CRITICAL
    """

    def __init__(self, criteria_path: Union[str, Path]) -> None:
        self.criteria_path = Path(criteria_path)
        self.criteria = self._load_criteria(self.criteria_path)

        envelope = self.criteria["flight_envelope"]
        self.max_altitude_m: float = float(envelope["max_altitude_m"])
        self.max_velocity_ms: float = float(envelope["max_velocity_ms"])
        self.low_battery_threshold: float = float(envelope["low_battery_threshold"])
        self.expected_failsafe_action: str = str(
            envelope["expected_failsafe_action"]
        ).upper()

        geofence = self.criteria.get("geofence", {})
        self.home_lat: float = float(geofence.get("home_latitude_deg", 0.0))
        self.home_lon: float = float(geofence.get("home_longitude_deg", 0.0))
        self.geofence_radius_m: float = float(geofence.get("radius_m", 500.0))

        # Previous-frame range-to-home used for RTL trend detection.
        self._prev_range_m: Optional[float] = None

        logger.info(
            "SILAssertionEngine ready — alt≤%.1f m, vel≤%.1f m/s, "
            "batt≥%.1f%%, failsafe=%s, geofence=%.0f m",
            self.max_altitude_m,
            self.max_velocity_ms,
            self.low_battery_threshold,
            self.expected_failsafe_action,
            self.geofence_radius_m,
        )

    # ------------------------------------------------------------------
    # Criteria loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_criteria(path: Path) -> Dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(f"Test criteria file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict) or "flight_envelope" not in data:
            raise ValueError(
                f"Invalid criteria file '{path}': "
                "missing required 'flight_envelope' section."
            )
        return data

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def haversine_m(
        lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        """Great-circle distance between two WGS-84 points in meters."""
        φ1, φ2 = math.radians(lat1), math.radians(lat2)
        Δφ = math.radians(lat2 - lat1)
        Δλ = math.radians(lon2 - lon1)
        a = (
            math.sin(Δφ / 2) ** 2
            + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2) ** 2
        )
        return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))

    def range_to_home(self, frame: TelemetryFrame) -> float:
        """Horizontal distance from the current position to home (meters)."""
        return self.haversine_m(
            self.home_lat, self.home_lon, frame.latitude, frame.longitude
        )

    # ------------------------------------------------------------------
    # Per-frame evaluation
    # ------------------------------------------------------------------

    def evaluate_frame(self, frame: TelemetryFrame) -> List[AssertionIssue]:
        """
        Run all assertion rules against a single telemetry frame.

        Returns a (possibly empty) list of ``AssertionIssue`` objects.
        """
        issues: List[AssertionIssue] = []

        # --- Altitude envelope -----------------------------------------
        if frame.altitude > self.max_altitude_m:
            issues.append(
                AssertionIssue(
                    timestamp=frame.timestamp,
                    severity="CRITICAL",
                    rule="MAX_ALTITUDE",
                    description=(
                        f"Altitude breach: {frame.altitude:.1f} m exceeds "
                        f"envelope limit of {self.max_altitude_m:.1f} m."
                    ),
                    measured_value=frame.altitude,
                    limit_value=self.max_altitude_m,
                )
            )

        # --- Velocity envelope -----------------------------------------
        if frame.velocity > self.max_velocity_ms:
            issues.append(
                AssertionIssue(
                    timestamp=frame.timestamp,
                    severity="HIGH",
                    rule="MAX_VELOCITY",
                    description=(
                        f"Velocity breach: {frame.velocity:.1f} m/s exceeds "
                        f"envelope limit of {self.max_velocity_ms:.1f} m/s."
                    ),
                    measured_value=frame.velocity,
                    limit_value=self.max_velocity_ms,
                )
            )

        # --- Geofence --------------------------------------------------
        range_m = self.range_to_home(frame)
        if range_m > self.geofence_radius_m:
            issues.append(
                AssertionIssue(
                    timestamp=frame.timestamp,
                    severity="CRITICAL",
                    rule="GEOFENCE",
                    description=(
                        f"Geofence breach: range-to-home {range_m:.1f} m "
                        f"exceeds radius {self.geofence_radius_m:.1f} m."
                    ),
                    measured_value=range_m,
                    limit_value=self.geofence_radius_m,
                )
            )

        # --- Low-battery / RTL failsafe --------------------------------
        if frame.battery <= self.low_battery_threshold:
            # Battery is critically low — expect RTL (closing range to home).
            if self.expected_failsafe_action == "RTL":
                rtl_mismatch = False
                detail = ""

                if self._prev_range_m is not None:
                    # Outbound motion while low on battery ⇒ RTL not active.
                    if range_m > self._prev_range_m + 1.0:  # >1 m tolerance
                        rtl_mismatch = True
                        detail = (
                            f"range increased from {self._prev_range_m:.1f} m "
                            f"to {range_m:.1f} m"
                        )
                # Also flag if still climbing while critically low.
                if frame.altitude > 5.0 and frame.velocity > 5.0 and (
                    self._prev_range_m is None or range_m >= (self._prev_range_m or 0)
                ):
                    # Aggressive outbound / high-energy profile under low batt.
                    if self._prev_range_m is not None and range_m >= self._prev_range_m:
                        rtl_mismatch = True
                        detail = detail or (
                            f"high-energy outbound profile "
                            f"(alt={frame.altitude:.1f} m, vel={frame.velocity:.1f} m/s)"
                        )

                if rtl_mismatch:
                    issues.append(
                        AssertionIssue(
                            timestamp=frame.timestamp,
                            severity="CRITICAL",
                            rule="LOW_BATTERY_RTL",
                            description=(
                                f"Low-battery RTL mismatch: battery "
                                f"{frame.battery:.1f}% ≤ "
                                f"{self.low_battery_threshold:.1f}% threshold "
                                f"but expected failsafe '{self.expected_failsafe_action}' "
                                f"not observed ({detail})."
                            ),
                            measured_value=frame.battery,
                            limit_value=self.low_battery_threshold,
                        )
                    )
            else:
                # Generic low-battery advisory when expected action is unknown.
                issues.append(
                    AssertionIssue(
                        timestamp=frame.timestamp,
                        severity="HIGH",
                        rule="LOW_BATTERY",
                        description=(
                            f"Battery {frame.battery:.1f}% at or below "
                            f"{self.low_battery_threshold:.1f}% threshold; "
                            f"expected failsafe '{self.expected_failsafe_action}'."
                        ),
                        measured_value=frame.battery,
                        limit_value=self.low_battery_threshold,
                    )
                )

        self._prev_range_m = range_m

        for issue in issues:
            logger.warning(
                "[%s] t=%.3f %s — %s",
                issue.severity,
                issue.timestamp,
                issue.rule,
                issue.description,
            )

        return issues

    def evaluate(
        self, frames: List[TelemetryFrame]
    ) -> EvaluationResult:
        """
        Evaluate an ordered sequence of telemetry frames.

        Resets internal RTL-trend state before starting so successive
        ``evaluate`` calls are independent.
        """
        self._prev_range_m = None
        result = EvaluationResult(frames_evaluated=len(frames))

        for frame in frames:
            frame_issues = self.evaluate_frame(frame)
            result.issues.extend(frame_issues)

        result.passed = len(result.issues) == 0
        logger.info(
            "Evaluation complete: %d frames, %d issues, passed=%s",
            result.frames_evaluated,
            len(result.issues),
            result.passed,
        )
        return result
