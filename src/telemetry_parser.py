"""
Telemetry frame parser for UAS SIL binary flight logs.

Frame layout (36 bytes, little-endian):
    offset  size  type    field
    ------  ----  ------  ---------
     0       8    double  timestamp   (Unix epoch seconds)
     8       8    double  latitude    (degrees WGS-84)
    16       8    double  longitude   (degrees WGS-84)
    24       4    float   altitude    (meters AGL)
    28       4    float   velocity    (meters / second)
    32       4    float   battery     (percent 0–100)
"""

from __future__ import annotations

import logging
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Iterator, List, Union

logger = logging.getLogger(__name__)

# Little-endian: d=double, f=float  →  8+8+8+4+4+4 = 36 bytes
FRAME_FORMAT = "<dddfff"
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)  # 36


class TelemetryParseError(ValueError):
    """Raised when a telemetry frame cannot be unpacked."""


@dataclass(frozen=True)
class TelemetryFrame:
    """Immutable representation of a single 36-byte SIL telemetry sample."""

    timestamp: float
    latitude: float
    longitude: float
    altitude: float
    velocity: float
    battery: float

    def to_dict(self) -> dict:
        """Serialize the frame to a plain dictionary."""
        return asdict(self)


class TelemetryParser:
    """
    Unpack fixed-length binary telemetry frames from SIL flight logs.

    Example
    -------
    >>> parser = TelemetryParser()
    >>> frames = parser.parse_file("data/sample_sil_flight_log.bin")
    >>> print(frames[0].altitude)
    """

    def __init__(self, frame_format: str = FRAME_FORMAT) -> None:
        self.frame_format = frame_format
        self.frame_size = struct.calcsize(frame_format)
        if self.frame_size != FRAME_SIZE:
            raise ValueError(
                f"Unexpected frame size {self.frame_size}; "
                f"expected {FRAME_SIZE} bytes for format '{FRAME_FORMAT}'."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_frame(self, data: bytes) -> TelemetryFrame:
        """
        Unpack a single 36-byte binary frame.

        Parameters
        ----------
        data:
            Exactly FRAME_SIZE bytes of little-endian telemetry.

        Returns
        -------
        TelemetryFrame
            Parsed field values.

        Raises
        ------
        TelemetryParseError
            If ``data`` is not exactly FRAME_SIZE bytes, or if struct
            unpacking fails for any reason.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TelemetryParseError(
                f"Frame data must be bytes-like, got {type(data).__name__}."
            )

        if len(data) != self.frame_size:
            raise TelemetryParseError(
                f"Invalid frame size: expected {self.frame_size} bytes, "
                f"got {len(data)} bytes."
            )

        try:
            values = struct.unpack(self.frame_format, data)
        except struct.error as exc:
            raise TelemetryParseError(
                f"Failed to unpack telemetry frame: {exc}"
            ) from exc

        frame = TelemetryFrame(
            timestamp=values[0],
            latitude=values[1],
            longitude=values[2],
            altitude=values[3],
            velocity=values[4],
            battery=values[5],
        )
        logger.debug("Parsed frame @ t=%.3f alt=%.1f m", frame.timestamp, frame.altitude)
        return frame

    def parse_stream(self, stream: BinaryIO) -> Iterator[TelemetryFrame]:
        """
        Yield frames by reading sequentially from a binary stream.

        Trailing incomplete bytes (short of a full frame) raise
        ``TelemetryParseError`` so truncated logs are never silently ignored.
        """
        while True:
            chunk = stream.read(self.frame_size)
            if not chunk:
                break
            if len(chunk) < self.frame_size:
                raise TelemetryParseError(
                    f"Truncated frame at end of stream: "
                    f"expected {self.frame_size} bytes, got {len(chunk)}."
                )
            yield self.parse_frame(chunk)

    def parse_file(self, path: Union[str, Path]) -> List[TelemetryFrame]:
        """
        Parse every frame from a binary flight-log file.

        Parameters
        ----------
        path:
            Filesystem path to a ``.bin`` SIL telemetry log.

        Returns
        -------
        list[TelemetryFrame]
            All successfully unpacked frames in chronological order.
        """
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Telemetry log not found: {path}")

        file_size = path.stat().st_size
        if file_size % self.frame_size != 0:
            raise TelemetryParseError(
                f"Log file size ({file_size} bytes) is not an integer "
                f"multiple of frame size ({self.frame_size} bytes)."
            )

        frames: List[TelemetryFrame] = []
        with path.open("rb") as fh:
            for frame in self.parse_stream(fh):
                frames.append(frame)

        logger.info("Parsed %d frames from %s", len(frames), path)
        return frames

    @staticmethod
    def pack_frame(
        timestamp: float,
        latitude: float,
        longitude: float,
        altitude: float,
        velocity: float,
        battery: float,
    ) -> bytes:
        """
        Pack field values into a 36-byte little-endian binary frame.

        Useful for generating mock SIL logs during development and testing.
        """
        return struct.pack(
            FRAME_FORMAT,
            timestamp,
            latitude,
            longitude,
            altitude,
            velocity,
            battery,
        )
