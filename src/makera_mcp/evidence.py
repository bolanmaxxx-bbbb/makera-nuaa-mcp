from __future__ import annotations

import json
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path

from .camera import Frame, utc_now
from .config import Settings


class EvidenceStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.runtime / "evidence"
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def prune(self):
        """Only delete generated artifacts in this exact evidence directory."""
        with self.lock:
            files = sorted((p for p in self.path.iterdir() if p.is_file()
                            and re.fullmatch(r"[0-9a-f]{32}\.(jpg|json|zip)", p.name)),
                           key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            for path in files:
                size = path.stat().st_size
                if (time.time() - path.stat().st_mtime > self.settings.retain_hours * 3600
                        or total > self.settings.max_disk_mb * 1024 * 1024):
                    path.unlink(missing_ok=True)
                    total -= size

    def save(self, frames: list[Frame], context: dict | None = None) -> dict:
        with self.lock:
            self.prune()
            observation_id = uuid.uuid4().hex
            entries = []
            for frame in frames:
                fid = uuid.uuid4().hex
                image_path = self.path / f"{fid}.jpg"
                image_path.write_bytes(frame.data)
                entries.append({**frame.metadata(), "file": image_path.name})
            observation = {"id": observation_id, "saved_at": utc_now(),
                           "frames": entries, "context": context or {}}
            path = self.path / f"{observation_id}.json"
            path.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
            return {**observation, "manifest_path": str(path)}

    def export(self, observation_id: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", observation_id):
            raise ValueError("Invalid observation ID")
        with self.lock:
            path = self.path / f"{observation_id}.json"
            observation = json.loads(path.read_text(encoding="utf-8"))
            images = []
            for item in observation["frames"]:
                name = item["file"]
                if not re.fullmatch(r"[0-9a-f]{32}\.jpg", name):
                    raise ValueError("Invalid evidence filename")
                image = self.path / name
                if not image.is_file():
                    raise FileNotFoundError("Evidence frame was removed by retention")
                images.append(image)
            dest = self.path / f"{observation_id}.zip"
            with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(path, "observation.json")
                for image in images:
                    archive.write(image, image.name)
            return {"observation_id": observation_id, "archive_path": str(dest), "bytes": dest.stat().st_size}
