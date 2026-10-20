import io
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from websockets.sync.server import serve

from makera_mcp.camera import Camera, CameraUnavailable, decode_frame
from makera_mcp.config import Settings
from makera_mcp.evidence import EvidenceStore
from makera_mcp.nc import inspect_nc


def jpeg(color="gray"):
    output = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(output, "JPEG")
    return output.getvalue()


class ObserverTests(unittest.TestCase):
    def test_bad_frames_and_unknown_source_time(self):
        with self.assertRaises(Exception):
            decode_frame(b"bad jpeg", 1)
        frame = decode_frame(jpeg("black"), 1)
        self.assertTrue(frame.quality["near_black"])
        self.assertIsNone(frame.metadata()["source_age_s"])
        self.assertFalse(frame.metadata()["source_timestamp_available"])

    def test_disconnect_never_serves_stored_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = Camera(Settings(Path(directory), "ws://127.0.0.1:1/ws_video", frame_timeout_s=0.1))
            camera.frames.append(decode_frame(jpeg(), 1))
            camera.sequence = 1
            try:
                with self.assertRaises(CameraUnavailable):
                    camera.fresh_frame(after=0, timeout=0.25)
            finally:
                camera.stop()

    def test_camera_stream_reconnect_and_shutdown(self):
        commands = []
        connections = []
        def handler(socket):
            connections.append(len(connections) + 1)
            number = len(connections)
            commands.append(socket.recv(timeout=2))
            if number == 1:
                socket.send(jpeg("gray"))
                time.sleep(0.05)
                socket.close()
                return
            try:
                for _ in range(80):
                    socket.send(jpeg("white"))
                    time.sleep(0.03)
            except Exception:
                pass
        with tempfile.TemporaryDirectory() as directory:
            with serve(handler, "127.0.0.1", 0) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                port = server.socket.getsockname()[1]
                camera = Camera(Settings(Path(directory), f"ws://127.0.0.1:{port}/ws_video", frame_timeout_s=0.4))
                try:
                    first = camera.fresh_frame(timeout=2)
                    time.sleep(0.2)
                    second = camera.fresh_frame(after=first.sequence, timeout=3)
                    self.assertGreater(second.sequence, first.sequence)
                    self.assertGreaterEqual(camera.status()["connection_generation"], 2)
                    self.assertIsNone(second.metadata()["source_captured_at"])
                    self.assertTrue(all(command == "start_stream" for command in commands))
                finally:
                    camera.stop()
                    server.shutdown()
                self.assertEqual(camera.status()["state"], "stopped")
                self.assertFalse(camera.thread.is_alive())

    def test_bounds_and_missing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = Camera(Settings(Path(directory)))
            with self.assertRaises(CameraUnavailable):
                camera.start()
            for count, interval in [(0, 1), (9, 1), (8, 5), (2, 0.01)]:
                with self.assertRaises(ValueError):
                    camera.sample(count, interval)

    def test_evidence_roundtrip_and_path_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            store = EvidenceStore(Settings(Path(directory)))
            frame = decode_frame(jpeg(), 1)
            observation = store.save([frame], {"task": "test"})
            archive = store.export(observation["id"])
            self.assertTrue(Path(archive["archive_path"]).is_file())
            with self.assertRaises(ValueError):
                store.export("../anything")
            manifest = Path(observation["manifest_path"])
            data = json.loads(manifest.read_text())
            data["frames"][0]["file"] = "../../private.jpg"
            manifest.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                store.export(observation["id"])

    def test_nc_inspection_does_not_claim_travel_or_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.nc"
            path.write_text("G21 G90\nG1X2Y-3 F100\nG91\nG1X2\nM3S1000 ; X999\n")
            result = inspect_nc(str(path))
            self.assertFalse(result["machining_approved"])
            self.assertEqual(result["literal_word_ranges"]["X"]["max"], 2)
            self.assertEqual(result["distance_mode_commands"], ["G90", "G91"])


if __name__ == "__main__":
    unittest.main()
