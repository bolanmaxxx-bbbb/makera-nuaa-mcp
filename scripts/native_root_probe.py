import ctypes
import json
from ctypes import wintypes
from pathlib import Path

import frida

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
device = frida.get_local_device()
processes = [process for process in device.enumerate_processes() if process.name.lower() == "makerastudio.exe"]
if len(processes) != 1:
    raise RuntimeError("Expected one Studio process")
process = processes[0]
windows = []
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
@callback_type
def visit(hwnd, _):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == process.pid and user32.IsWindowVisible(hwnd):
        title = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(hwnd, title, 1024)
        if title.value.startswith("MakeraStudio"):
            windows.append({"hwnd": int(hwnd), "title": title.value})
    return True
user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
user32.EnumWindows(visit, 0)
if len(windows) != 1:
    raise RuntimeError(f"Expected one main window: {windows}")
session = device.attach(process.pid)
try:
    script = session.create_script((Path(__file__).resolve().parents[1] / "src/makera_mcp/native_agent.js").read_text(encoding="utf-8"))
    script.load()
    result = {"pid": process.pid, "window": windows[0], "native": script.exports_sync.read_objects(hex(windows[0]["hwnd"]))}
    (Path(__file__).resolve().parents[1] / "runtime/native-objects-probe.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"pid":process.pid,"root_class":result["native"]["rootClass"],
                      "root_methods":result["native"]["rootMethods"],"object_count":len(result["native"]["objects"]),
                      "native_managers":[obj for obj in result["native"]["objects"] if any(key in obj["type"].lower() for key in ['project','manager','scheme','toolpath','localserver'])][:35]},ensure_ascii=False,indent=2))
finally:
    session.detach()
