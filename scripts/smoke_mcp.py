"""Real stdio MCP handshake and optionally camera sampling. No machine motion."""
import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


async def smoke(live: bool):
    output = {"mode": "live_camera" if live else "protocol_only", "calls": []}
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "makera_mcp.server"], cwd=str(ROOT),
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            output["tools"] = [tool.name for tool in tools.tools]
            catalog = json.loads((ROOT / "skills" / "makera-studio-cam" / "assets" / "mcp-tool-map.json").read_text(encoding="utf-8"))
            expected = {entry["name"] for entry in catalog["tools"]}
            assert set(output["tools"]) == expected, "Exposed tools differ from the skill catalog"
            assert {"create_cam_task", "get_next_cam_action", "revise_cam_task"}.issubset(output["tools"])
            for name, arguments in [("get_capabilities", {}), ("get_studio_state", {}), ("get_camera_status", {})]:
                result = await client.call_tool(name, arguments)
                assert not result.isError, result
                output["calls"].append({"tool": name, "result": [json.loads(block.text) for block in result.content if block.type == "text"]})
            if live:
                await client.call_tool("set_task_context", {"task_id": "initial-validation", "operation": "stationary camera observation; no machining"})
                result = await client.call_tool("get_observation_bundle", {"count": 3, "interval_s": 0.5})
                assert not result.isError, result
                images = [block for block in result.content if block.type == "image"]
                assert len(images) == 3
                sizes = []
                for block in images:
                    with Image.open(io.BytesIO(base64.b64decode(block.data))) as image:
                        image.load()
                        sizes.append(list(image.size))
                evidence = json.loads(next(block.text for block in result.content if block.type == "text"))["observation"]
                sequences = [entry["sequence"] for entry in evidence["frames"]]
                assert sequences == sorted(set(sequences))
                exported = await client.call_tool("export_event_evidence", {"observation_id": evidence["id"]})
                assert not exported.isError
                output["live_observation"] = {"id": evidence["id"], "manifest_path": evidence["manifest_path"],
                                              "image_sizes": sizes, "sequences": sequences,
                                              "first_image": str(Path(evidence["manifest_path"]).parent / evidence["frames"][0]["file"]),
                                              "context": evidence["context"]}
                output["export"] = [json.loads(block.text) for block in exported.content if block.type == "text"]
                started = await client.call_tool("start_camera", {})
                assert not started.isError
                capture = await client.call_tool("capture_frame", {})
                assert not capture.isError
                assert len([block for block in capture.content if block.type == "image"]) == 1
                sampled = await client.call_tool("sample_frames", {"count": 2, "interval_s": 0.2})
                assert not sampled.isError
                assert len([block for block in sampled.content if block.type == "image"]) == 2
                events = await client.call_tool("list_visual_events", {})
                assert not events.isError
                output["positive_tools_verified"] = ["start_camera", "capture_frame", "sample_frames", "list_visual_events"]
                result = await client.call_tool("sample_frames", {"count": 9})
                assert result.isError, "Invalid sample count must fail"
                await client.call_tool("stop_camera", {})
                status = await client.call_tool("get_camera_status", {})
                assert json.loads(status.content[0].text)["state"] == "stopped"
                output["invalid_count_rejected"] = True
                output["camera_shutdown_verified"] = True
    destination = ROOT / "runtime" / ("live-validation.json" if live else "protocol-validation.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    arguments = parser.parse_args()
    anyio.run(smoke, arguments.live)
