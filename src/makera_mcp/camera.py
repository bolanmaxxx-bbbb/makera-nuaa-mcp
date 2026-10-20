from __future__ import annotations

import hashlib
import io
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from PIL import Image, ImageStat
from websockets.sync.client import connect

from .config import Settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class CameraUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Frame:
    sequence: int
    received_at: str
    received_monotonic: float
    data: bytes
    width: int
    height: int
    sha256: str
    quality: dict

    def metadata(self) -> dict:
        return {
            "sequence": self.sequence,
            "received_at": self.received_at,
            "received_age_s": round(max(0, time.monotonic() - self.received_monotonic), 3),
            "source_captured_at": None,
            "source_age_s": None,
            "end_to_end_latency_s": None,
            "source_timestamp_available": False,
            "source": "makera_ws_video",
            "width": self.width, "height": self.height,
            "sha256": self.sha256, "mime_type": "image/jpeg",
            "quality": self.quality,
        }


def decode_frame(payload: bytes, sequence: int) -> Frame:
    received_at, received_mono = utc_now(), time.monotonic()
    if not isinstance(payload, bytes) or len(payload) > 4 * 1024 * 1024:
        raise ValueError("Expected a binary JPEG no larger than 4 MiB")
    with Image.open(io.BytesIO(payload)) as image:
        if image.format != "JPEG" or image.width * image.height > 4096 * 4096:
            raise ValueError("Unsupported camera image format or dimensions")
        image.load()
        gray = image.convert("L")
        gray.thumbnail((160, 120))
        stats = ImageStat.Stat(gray)
        hist = gray.histogram()
        pixels = sum(hist)
        mean, stddev = stats.mean[0], stats.stddev[0]
        quality = {
            "mean_luma": round(mean, 2), "stddev_luma": round(stddev, 2),
            "dark_pixel_fraction": round(sum(hist[:12]) / pixels, 4),
            "bright_pixel_fraction": round(sum(hist[245:]) / pixels, 4),
            "near_black": mean < 8 and stddev < 8,
            "low_contrast": stddev < 5,
            "interpretation": "Image statistics only; no machining anomaly classifier is enabled",
        }
        return Frame(sequence, received_at, received_mono, payload, image.width,
                     image.height, hashlib.sha256(payload).hexdigest(), quality)


class Camera:
    """One receiver per MCP process; disconnect never returns a cached frame as live."""

    def __init__(self, settings: Settings, on_event: Callable | None = None):
        self.settings = settings
        self.frames: deque[Frame] = deque(maxlen=settings.buffer_frames)
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.socket = None
        self.state = "stopped"
        self.last_error = None
        self.sequence = 0
        self.connection_generation = 0
        self.latest_generation = -1
        self.repeated_frames = 0
        self.on_event = on_event or (lambda *args, **kwargs: None)

    def start(self) -> dict:
        if not self.settings.camera_url:
            raise CameraUnavailable("Camera URL is not configured; no network scan is performed")
        with self.condition:
            if self.thread and self.thread.is_alive():
                return self.status()
            self.stop_event.clear()
            self.state = "connecting"
            self.thread = threading.Thread(target=self._receive, name="makera-camera", daemon=True)
            self.thread.start()
        return self.status()

    def stop(self) -> dict:
        self.stop_event.set()
        with self.condition:
            sock = self.socket
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=7)
        with self.condition:
            self.state = "stopped"
            self.condition.notify_all()
        return self.status()

    def _receive(self):
        backoff = 0.5
        while not self.stop_event.is_set():
            try:
                with self.condition:
                    self.state = "connecting"
                with connect(self.settings.camera_url, open_timeout=4, close_timeout=1,
                             max_size=4 * 1024 * 1024, max_queue=2,
                             ping_interval=20, ping_timeout=10, proxy=None) as sock:
                    with self.condition:
                        self.socket = sock
                        self.connection_generation += 1
                        self.state = "awaiting_frame"
                        self.frames.clear()
                        self.repeated_frames = 0
                    # Observed in Studio's own log; camera subscription only.
                    sock.send("start_stream")
                    while not self.stop_event.is_set():
                        payload = sock.recv(timeout=self.settings.frame_timeout_s)
                        if isinstance(payload, str):
                            continue
                        frame = decode_frame(payload, self.sequence + 1)
                        with self.condition:
                            previous = self.frames[-1] if self.frames else None
                            self.repeated_frames = self.repeated_frames + 1 if previous and previous.sha256 == frame.sha256 else 0
                            self.sequence += 1
                            self.frames.append(frame)
                            self.latest_generation = self.connection_generation
                            self.state = "streaming"
                            self.last_error = None
                            self.condition.notify_all()
                        backoff = 0.5
            except Exception as exc:
                with self.condition:
                    self.state = "disconnected"
                    # Avoid echoing credentials or complete network error URLs.
                    self.last_error = type(exc).__name__
                    self.condition.notify_all()
                self.on_event("camera_disconnected", {"error_type": type(exc).__name__})
            finally:
                with self.condition:
                    self.socket = None
            if self.stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, 8)

    def status(self) -> dict:
        with self.condition:
            latest = self.frames[-1] if self.frames else None
            return {
                "state": self.state,
                "configured": bool(self.settings.camera_url),
                "source": "makera_ws_video",
                "received_frames": self.sequence,
                "connection_generation": self.connection_generation,
                "buffered_frames": len(self.frames),
                "last_error_type": self.last_error,
                "latest_frame": latest.metadata() if latest else None,
                "identical_consecutive_frames": self.repeated_frames,
                "freeze_assessment": "unknown; identical images may be a stationary scene",
                "true_source_freshness": "unknown; no source timestamp in observed JPEG messages",
            }

    def fresh_frame(self, after: int | None = None, timeout: float = 7) -> Frame:
        self.start()
        deadline = time.monotonic() + timeout
        with self.condition:
            baseline = self.sequence if after is None else after
            while True:
                latest = self.frames[-1] if self.frames else None
                if (self.state == "streaming" and latest and latest.sequence > baseline
                        and self.latest_generation == self.connection_generation
                        and time.monotonic() - latest.received_monotonic <= 1.5):
                    return latest
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CameraUnavailable(f"No new camera frame within {timeout:g}s; state={self.state}; error={self.last_error}")
                self.condition.wait(remaining)

    def sample(self, count: int = 3, interval_s: float = 1.0) -> list[Frame]:
        if not 1 <= count <= 8 or not 0.2 <= interval_s <= 5 or (count - 1) * interval_s > 20:
            raise ValueError("count must be 1..8, interval 0.2..5s, sampling window <=20s")
        result = [self.fresh_frame()]
        for _ in range(count - 1):
            if self.stop_event.wait(interval_s):
                raise CameraUnavailable("Camera was stopped during sampling")
            result.append(self.fresh_frame(after=result[-1].sequence))
        return result
