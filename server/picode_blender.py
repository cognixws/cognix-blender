#!/usr/bin/env python3
"""picode-blender MCP server (stdio, Python standard library only).

Each tool sends one request to the bridge running inside Blender
(blender/bridge.py) and returns its answer. `blender_open` starts Blender with
the bridge; on WSL it starts the Windows blender.exe, which needs WSL's
mirrored networking to be reachable at 127.0.0.1.
"""

import base64
import glob
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(os.path.dirname(HERE), "blender", "bridge.py")
STATE_DIR = os.path.join(os.path.expanduser("~"), ".picode-blender")
STATE = os.path.join(STATE_DIR, "bridge.json")
OUT_DIR = os.path.join(STATE_DIR, "out")
VERSION = "0.4.0"
BRIDGE_VERSION = 3  # blender/bridge.py must report at least this


class ToolError(Exception):
    pass


# ---------- where Blender is, and how paths cross into it ----------

def in_wsl():
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def find_blender():
    exe = os.environ.get("PICODE_BLENDER_EXE")
    if exe:
        return exe
    found = shutil.which("blender")
    if found:
        return found
    if in_wsl():
        cands = glob.glob("/mnt/c/Program Files/Blender Foundation/Blender */blender.exe")

        def ver(p):
            v = p.split("Blender ")[-1].split("/")[0]
            return [int(x) for x in v.split(".") if x.isdigit()]
        if cands:
            return sorted(cands, key=ver)[-1]
    raise ToolError("Blender was not found. Install Blender 4.2+ or set PICODE_BLENDER_EXE to its executable.")


def windows_exe(exe):
    return exe.lower().endswith(".exe")


def to_blender_path(path, exe):
    """A path Blender can open: Windows form when Blender is a Windows program."""
    if not windows_exe(exe):
        return path
    return subprocess.run(["wslpath", "-w", path], capture_output=True, text=True, check=True).stdout.strip()


def check_networking(exe):
    if not (windows_exe(exe) and in_wsl()):
        return
    try:
        mode = subprocess.run(["wslinfo", "--networking-mode"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return
    if mode and mode != "mirrored":
        raise ToolError(
            "WSL networking is %r, so this extension (in WSL) cannot reach Blender (on Windows) at 127.0.0.1. "
            "Add `networkingMode=mirrored` under [wsl2] in %%UserProfile%%\\.wslconfig, run `wsl --shutdown`, and start PiCode again." % mode)


# ---------- the bridge ----------

def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def call(op, params=None, timeout=600, state=None):
    st = state or load_state()
    if not st:
        raise ToolError("Blender is not open — call blender_open first.")
    try:
        with socket.create_connection(("127.0.0.1", st["port"]), timeout=5) as s:
            s.settimeout(timeout)
            f = s.makefile("rwb")
            f.write((json.dumps({"token": st["token"], "id": 1, "op": op, "params": params or {}}) + "\n").encode())
            f.flush()
            line = f.readline()
    except OSError as e:
        raise ToolError("Blender's bridge is not answering (%s). If Blender was closed, call blender_open." % e)
    if not line:
        raise ToolError("Blender closed the connection.")
    answer = json.loads(line)
    if not answer.get("ok"):
        raise ToolError(answer.get("error", "the bridge failed"))
    return answer["result"]


def bridge_alive():
    try:
        return call("ping", timeout=5)
    except ToolError:
        return None


def stale_note(alive):
    if alive and alive.get("bridge", 0) < BRIDGE_VERSION:
        return ("This Blender was opened by an older picode-blender, so the newest tools (build, group, duplicate) "
                "are missing there. Save your work (save_file), then blender_close and blender_open.")
    return None


def blender_open(args):
    alive = bridge_alive()
    if alive:
        note = stale_note(alive)
        return {"already_open": True, **alive, **({"warning": note} if note else {})}
    exe = find_blender()
    check_networking(exe)
    os.makedirs(STATE_DIR, exist_ok=True)
    token = secrets.token_hex(16)
    port_file = os.path.join(STATE_DIR, "port-" + token)
    cmd = [exe]
    if args.get("headless"):
        cmd.append("-b")
    if args.get("file"):
        cmd.append(to_blender_path(os.path.abspath(args["file"]), exe))
    cmd += ["--python", to_blender_path(BRIDGE, exe), "--", "--port", "0", "--token", token, "--port-file", to_blender_path(port_file, exe)]
    log = open(os.path.join(STATE_DIR, "blender.log"), "ab")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            start_new_session=True, cwd=os.path.expanduser("~"))
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise ToolError("Blender exited while starting (code %s); see %s" % (proc.returncode, os.path.join(STATE_DIR, "blender.log")))
        try:
            with open(port_file) as f:
                port = int(f.read().strip())
        except (OSError, ValueError):
            time.sleep(0.5)
            continue
        os.remove(port_file)
        state = {"port": port, "token": token, "pid": proc.pid, "exe": exe, "headless": bool(args.get("headless"))}
        with open(STATE, "w") as f:
            json.dump(state, f)
        os.chmod(STATE, 0o600)
        while time.time() < deadline:
            try:
                return {"already_open": False, **call("ping", timeout=5, state=state)}
            except ToolError:
                time.sleep(0.5)
    raise ToolError("Blender started but its bridge did not answer within 90 s; see %s" % os.path.join(STATE_DIR, "blender.log"))


def blender_close(args):
    st = load_state()
    if not st:
        return {"closed": False, "reason": "no Blender opened by this extension"}
    try:
        call("run_python", {"code": "import bpy\nbpy.app.timers.register(lambda: bpy.ops.wm.quit_blender(), first_interval=0.2)"}, timeout=5, state=st)
    except ToolError:
        pass
    try:
        os.kill(st["pid"], 15)
    except OSError:
        pass
    os.remove(STATE)
    return {"closed": True}


def out_path(kind, ext):
    os.makedirs(OUT_DIR, exist_ok=True)
    return os.path.join(OUT_DIR, "%s-%s.%s" % (kind, time.strftime("%Y%m%d-%H%M%S"), ext))


def image_result(path, meta):
    for _ in range(50):  # a Windows-side write can land a moment later
        if os.path.exists(path) and os.path.getsize(path) > 0:
            break
        time.sleep(0.1)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return [{"type": "image", "data": data, "mimeType": "image/png"},
            {"type": "text", "text": json.dumps({**meta, "path": path})}]


def with_paths(op, keys):
    """Translate path arguments into Blender's form, then call the bridge."""
    def run(args):
        st = load_state()
        if not st:
            raise ToolError("Blender is not open — call blender_open first.")
        params = dict(args)
        for k in keys:
            if params.get(k):
                params[k] = to_blender_path(os.path.abspath(params[k]), st["exe"])
        return call(op, params)
    return run


def render(args):
    st = load_state()
    if not st:
        raise ToolError("Blender is not open — call blender_open first.")
    path = os.path.abspath(args.get("path") or out_path("render", "png"))
    params = {k: v for k, v in args.items() if k != "path"}
    params["path"] = to_blender_path(path, st["exe"])
    meta = call("render", params, timeout=1800)
    return image_result(path, meta)


def viewport_shot(args):
    st = load_state()
    if not st:
        raise ToolError("Blender is not open — call blender_open first.")
    path = out_path("viewport", "png")
    meta = call("viewport_shot", {"path": to_blender_path(path, st["exe"])})
    return image_result(path, meta)


def passthrough(op):
    def run(args):
        try:
            return call(op, args)
        except ToolError as e:
            if "unknown op" in str(e):
                raise ToolError(stale_note({"bridge": 0}))
            raise
    return run


# ---------- tools ----------

VEC3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
COLOR = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 4, "description": "linear RGB(A), 0–1"}
TRANSFORM = {
    "location": {**VEC3, "description": "x, y, z in meters"},
    "rotation_deg": {**VEC3, "description": "Euler x, y, z in degrees"},
    "scale": {"description": "one number or [x, y, z]", "anyOf": [{"type": "number"}, VEC3]},
}


def tool(name, description, props=None, required=None, run=None):
    schema = {"type": "object", "properties": props or {}}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema, "run": run}


TOOLS = [
    tool("blender_open", "Open Blender with the PiCode bridge (or report that it is already open). Call this first. headless=true runs without a window (faster, no viewport).",
         {"headless": {"type": "boolean"}, "file": {"type": "string", "description": "a .blend file to open"}}, run=blender_open),
    tool("blender_close", "Close the Blender this extension opened.", run=blender_close),
    tool("blender_status", "Is Blender's bridge answering; Blender version; the open file.", run=lambda a: bridge_alive() or {"open": False}),
    tool("scene_info", "Objects (with transforms, dimensions, materials, modifiers), lights, cameras, materials, render settings.", run=passthrough("scene_info")),
    tool("new_scene", "Start a new empty scene (discards unsaved changes). empty=false keeps Blender's default cube, light and camera.",
         {"empty": {"type": "boolean"}}, run=passthrough("new_scene")),
    tool("object_create", "Add a primitive mesh. dimensions sets its size in meters and applies it to the mesh (scale stays 1, so bevels and thickness keep their real size); apply_scale=true does the same after scale.",
         {"primitive": {"type": "string", "enum": ["cube", "sphere", "ico_sphere", "cylinder", "cone", "torus", "plane", "monkey"]},
          "name": {"type": "string"}, "smooth": {"type": "boolean"}, "dimensions": {**VEC3, "description": "size x, y, z in meters"},
          "apply_scale": {"type": "boolean"}, **TRANSFORM}, ["primitive"], passthrough("object_create")),
    tool("object_edit", "Change an object: transform, dimensions, apply_scale, rename, parent (\"\" clears), smooth shading, or delete=true.",
         {"name": {"type": "string"}, "rename": {"type": "string"}, "parent": {"type": "string"}, "smooth": {"type": "boolean"},
          "dimensions": VEC3, "apply_scale": {"type": "boolean"}, "delete": {"type": "boolean"}, **TRANSFORM}, ["name"], passthrough("object_edit")),
    tool("world_set", "Background and exposure: world color (linear RGB) and strength; color management view_transform (AgX, Filmic, Standard), look and exposure.",
         {"color": COLOR, "strength": {"type": "number"}, "view_transform": {"type": "string"}, "look": {"type": "string"},
          "exposure": {"type": "number"}}, run=passthrough("world_set")),
    tool("modifier_add", "Add a modifier to an object. settings are the modifier's own Blender properties, e.g. BEVEL {\"width\":0.05,\"segments\":3}, SUBSURF {\"levels\":2}, ARRAY {\"count\":5}, BOOLEAN {\"operation\":\"DIFFERENCE\",\"object\":\"Cutter\"}.",
         {"object": {"type": "string"}, "type": {"type": "string", "description": "BEVEL, SUBSURF, ARRAY, MIRROR, SOLIDIFY, BOOLEAN, …"},
          "name": {"type": "string"}, "settings": {"type": "object"}}, ["object", "type"], passthrough("modifier_add")),
    tool("material_set", "Create or update a Principled BSDF material and assign it to an object.",
         {"object": {"type": "string"}, "material": {"type": "string", "description": "material name (reused if it exists)"},
          "color": COLOR, "metallic": {"type": "number"}, "roughness": {"type": "number"}, "transmission": {"type": "number"},
          "alpha": {"type": "number"}, "emission_color": COLOR, "emission_strength": {"type": "number"}}, ["object"], passthrough("material_set")),
    tool("light_set", "Add or change a light (POINT, SUN, SPOT, AREA); look_at aims it.",
         {"name": {"type": "string"}, "type": {"type": "string"}, "energy": {"type": "number", "description": "watts (sun: strength)"},
          "color": COLOR, "size": {"type": "number"}, "look_at": VEC3, **TRANSFORM}, run=passthrough("light_set")),
    tool("camera_set", "Add or change a camera, make it the scene camera; look_at aims it.",
         {"name": {"type": "string"}, "lens_mm": {"type": "number"}, "look_at": VEC3, **TRANSFORM}, run=passthrough("camera_set")),
    tool("build", "Run many modeling steps in ONE call, in order — use it instead of dozens of single calls or run_python for scenes with many parts. Each step is {\"op\": ..., \"params\": {...}} with op one of object_create, object_edit, modifier_add, material_set, light_set, camera_set, group, duplicate, world_set, and params exactly as that tool takes. Stops at the first failing step and says which.",
         {"steps": {"type": "array", "items": {"type": "object", "properties": {"op": {"type": "string"}, "params": {"type": "object"}}, "required": ["op"]}}},
         ["steps"], passthrough("batch")),
    tool("group", "Parent objects under a new empty (an assembly: a desk with its chair and monitor), so it moves, rotates and duplicates as one. location defaults to the objects' center on the floor.",
         {"name": {"type": "string"}, "objects": {"type": "array", "items": {"type": "string"}}, "location": VEC3}, ["name", "objects"], passthrough("group")),
    tool("duplicate", "Copy an object — or a group with everything under it — count times, each copy moved by offset (meters) and turned by rotation_deg from the previous one. names renames the copies; linked=true shares mesh data.",
         {"name": {"type": "string"}, "count": {"type": "integer"}, "offset": VEC3, "rotation_deg": VEC3,
          "names": {"type": "array", "items": {"type": "string"}}, "linked": {"type": "boolean"}}, ["name"], passthrough("duplicate")),
    tool("render", "Render the scene camera to a PNG and return the image. engine: eevee, cycles or workbench.",
         {"engine": {"type": "string"}, "resolution": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
          "samples": {"type": "integer"}, "path": {"type": "string", "description": "where to save (default ~/.picode-blender/out)"}}, run=render),
    tool("viewport_shot", "Capture Blender's 3D viewport as an image (needs a window, not headless).", run=viewport_shot),
    tool("export", "Export the scene: glb, gltf, obj, fbx or stl.",
         {"format": {"type": "string"}, "path": {"type": "string"}}, ["path"], with_paths("export", ["path"])),
    tool("save_file", "Save the .blend (to path, or where it was opened from).",
         {"path": {"type": "string"}}, run=with_paths("save_file", ["path"])),
    tool("run_python", "Run Python inside Blender with bpy available. Assign `result` to return a value; print() output is returned too. Use it for anything the other tools do not cover.",
         {"code": {"type": "string"}}, ["code"], passthrough("run_python")),
]
BY_NAME = {t["name"]: t for t in TOOLS}


# ---------- MCP over stdio ----------

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return
    if method == "initialize":
        return send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "blender", "version": VERSION},
            "instructions": "Blender tools: call blender_open first, then build with object_create, modifier_add, material_set, light_set and camera_set; check with scene_info and render (returns the image).",
        }})
    if method == "ping":
        return send({"jsonrpc": "2.0", "id": mid, "result": {}})
    if method == "tools/list":
        return send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {k: v for k, v in t.items() if k != "run"} for t in TOOLS]}})
    if method == "tools/call":
        t = BY_NAME.get(params.get("name"))
        if t is None:
            return send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool %r" % params.get("name")}})
        try:
            out = t["run"](params.get("arguments") or {})
            content = out if isinstance(out, list) else [{"type": "text", "text": json.dumps(out, indent=1)}]
            return send({"jsonrpc": "2.0", "id": mid, "result": {"content": content}})
        except ToolError as e:
            return send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}})
        except Exception as e:  # noqa: BLE001 — report, never crash the server
            return send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, e)}], "isError": True}})
    send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found: %s" % method}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        handle(msg)


if __name__ == "__main__":
    main()
