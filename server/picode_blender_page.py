#!/usr/bin/env python3
"""picode-blender's background program (PiCode ADR-0230: process, pages,
commands). PiCode starts it while the extension is on in a workspace and is
the only caller: every request carries X-PiCode-Proxy-Secret, the value
PiCode gave this process in PICODE_EXT_PROXY_SECRET. It talks to Blender
through the same bridge as the MCP server (picode_blender.py).

    GET  /status     is Blender open, which file, what is in the scene
    POST /snapshot   a small picture of Blender's 3D viewport
    POST /open       start Blender with a .blend file (never replaces one)
"""

import base64
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import picode_blender as pb  # noqa: E402

SECRET = os.environ.get("PICODE_EXT_PROXY_SECRET", "")
PORT = int(os.environ.get("PICODE_EXT_PORT", "0"))
SNAPSHOT_WIDTH = 800  # keeps the picture well under PiCode's 1 MiB relay


def file_name(path):
    """The file's name from a Linux or a Windows path (Blender on WSL
    reports \\\\wsl.localhost\\... paths)."""
    return re.split(r"[\\/]", path or "")[-1]


def status():
    alive = pb.bridge_alive()
    if not alive:
        return {"open": False}
    out = {"open": True, "blender": alive.get("blender"), "file": alive.get("file"), "background": alive.get("background")}
    note = pb.stale_note(alive)
    if note:
        out["warning"] = note
    try:
        info = pb.call("scene_info", timeout=15)
        out["objects"] = [{"name": o.get("name"), "type": o.get("type")} for o in info.get("objects", [])][:200]
        out["scene"] = info.get("scene")
    except pb.ToolError as e:
        out["error"] = str(e)
    return out


def snapshot():
    """Viewport screenshot, scaled down inside Blender."""
    st = pb.load_state()
    alive = pb.bridge_alive() if st else None
    if not alive:
        raise pb.ToolError("Blender is not open. Ask an agent to open it, or open a .blend file with Blender from PiCode.")
    if alive.get("background"):
        raise pb.ToolError("Blender is running without a window, so there is no viewport to capture. Ask an agent to render instead.")
    full = pb.out_path("viewport", "png")
    small = full[:-4] + "-small.png"
    pb.call("viewport_shot", {"path": pb.to_blender_path(full, st["exe"])})
    code = (
        "import bpy\n"
        "img = bpy.data.images.load(%r)\n"
        "w, h = img.size\n"
        "if w > %d:\n"
        "    img.scale(%d, max(1, int(h * %d / w)))\n"
        "img.filepath_raw = %r\n"
        "img.file_format = 'PNG'\n"
        "img.save()\n"
        "bpy.data.images.remove(img)\n"
    ) % (pb.to_blender_path(full, st["exe"]), SNAPSHOT_WIDTH, SNAPSHOT_WIDTH, SNAPSHOT_WIDTH, pb.to_blender_path(small, st["exe"]))
    pb.call("run_python", {"code": code}, timeout=60)
    for _ in range(50):  # a Windows-side write can land a moment later
        if os.path.exists(small) and os.path.getsize(small) > 0:
            break
        time.sleep(0.1)
    with open(small, "rb") as f:
        return {"path": full, "image": base64.b64encode(f.read()).decode()}


def open_file(path):
    if not path or not path.lower().endswith(".blend"):
        raise pb.ToolError("Choose a .blend file.")
    if not os.path.isfile(path):
        raise pb.ToolError("That file is not there any more: %s" % path)
    alive = pb.bridge_alive()
    if alive:
        now = alive.get("file") or ""
        if now and file_name(now) == file_name(path):
            return {"opened": False, "message": "Blender already has this file open."}
        raise pb.ToolError("Blender already has %s open. Save it and close Blender first, so nothing is lost."
                           % (file_name(now) if now else "an unsaved scene"))
    pb.blender_open({"file": path})
    return {"opened": True, "message": "Opened %s in Blender." % os.path.basename(path)}


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="application/json"):
        data = (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def allowed(self):
        if SECRET and self.headers.get("X-PiCode-Proxy-Secret") == SECRET:
            return True
        self.reply(403, {"message": "only PiCode may call this program"})
        return False

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        if not self.allowed():
            return
        if self.path == "/status":
            return self.reply(200, status())
        self.reply(404, {"message": "no such route"})

    def do_POST(self):
        if not self.allowed():
            return
        req = self.body()
        # A PiCode command (menu, palette) sends {command, context} and
        # shows a text answer; the page sends its own fields.
        from_command = "command" in req
        try:
            if self.path == "/snapshot":
                shot = snapshot()
                if from_command:
                    return self.reply(200, "Saved a viewport snapshot to %s" % shot["path"], "text/plain")
                return self.reply(200, shot)
            if self.path == "/open":
                path = req.get("path") or (req.get("context") or {}).get("path")
                res = open_file(path)
                if from_command:
                    return self.reply(200, res["message"], "text/plain")
                return self.reply(200, res)
        except pb.ToolError as e:
            return self.reply(409, {"message": str(e)})
        except Exception as e:  # noqa: BLE001 — answer, never crash
            return self.reply(500, {"message": "%s: %s" % (type(e).__name__, e)})
        self.reply(404, {"message": "no such route"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("picode-blender page server on 127.0.0.1:%d" % PORT, flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
