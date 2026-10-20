from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    root: Path
    camera_url: str | None = None
    frame_timeout_s: float = 5.0
    buffer_frames: int = 40
    max_disk_mb: int = 256
    retain_hours: int = 72

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @classmethod
    def load(cls) -> "Settings":
        path = Path(os.environ.get("MAKERA_MCP_CONFIG", ROOT / "config.local.json"))
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        url = os.environ.get("MAKERA_CAMERA_URL") or values.get("camera_url")
        if url:
            parsed = urlsplit(url)
            if parsed.scheme not in ("ws", "wss") or parsed.path != "/ws_video":
                raise ValueError("Expected a verified ws(s) camera endpoint ending /ws_video")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Embedded credentials, query and fragment are not supported")
            address = ipaddress.ip_address(parsed.hostname or "")
            if not (address.is_private or address.is_loopback):
                raise ValueError("Only explicitly configured local/LAN camera addresses are supported")
        settings = cls(
            root=ROOT,
            camera_url=url,
            buffer_frames=int(values.get("buffer_frames", 40)),
            max_disk_mb=int(values.get("max_disk_mb", 256)),
            retain_hours=int(values.get("retain_hours", 72)),
        )
        if not 2 <= settings.buffer_frames <= 120:
            raise ValueError("buffer_frames must be 2..120")
        if not 16 <= settings.max_disk_mb <= 2048 or not 1 <= settings.retain_hours <= 720:
            raise ValueError("Invalid disk retention settings")
        return settings
