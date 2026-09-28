"""picode-blender bridge: loaded by Blender at start (`--python bridge.py -- --port N --token T`).

It listens on 127.0.0.1 and runs each request on Blender's main thread, because
bpy is not thread-safe. With a window, a timer drains the request queue; in
background mode (`-b`) the main thread serves requests itself.

Protocol: one JSON object per line.
  request:  {"token": "...", "id": 1, "op": "scene_info", "params": {...}}
  answer:   {"id": 1, "ok": true, "result": {...}}  or  {"id": 1, "ok": false, "error": "..."}
"""

import io
import json
import math
import os
import queue
import socket
import sys
import threading
import traceback
from contextlib import redirect_stdout

import bpy

HOST = "127.0.0.1"
BRIDGE_VERSION = 3  # the server compares it: an older Blender session lacks newer ops
_requests = queue.Queue()


def _args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {"--port": "0", "--token": "", "--port-file": ""}
    for i, a in enumerate(argv):
        if a in opts and i + 1 < len(argv):
            opts[a] = argv[i + 1]
    return int(opts["--port"]), opts["--token"], opts["--port-file"]


# ---------- helpers ----------

def _vec(v, n=3, default=0.0):
    if v is None:
        return None
    v = list(v)
    if len(v) != n:
        raise ValueError("expected %d numbers, got %d" % (n, len(v)))
    return [float(x) for x in v]


def _obj(name):
    o = bpy.data.objects.get(name)
    if o is None:
        raise ValueError("no object named %r" % name)
    return o


def _describe(o):
    bpy.context.view_layer.update()  # dimensions follow a scale set this call
    d = {
        "name": o.name,
        "type": o.type,
        "location": [round(x, 4) for x in o.location],
        "rotation_deg": [round(math.degrees(x), 2) for x in o.rotation_euler],
        "scale": [round(x, 4) for x in o.scale],
        "dimensions": [round(x, 4) for x in o.dimensions],
        "parent": o.parent.name if o.parent else None,
        "visible": not o.hide_get() if hasattr(o, "hide_get") else True,
    }
    if o.type == "MESH":
        d["vertices"] = len(o.data.vertices)
        d["materials"] = [m.name for m in o.data.materials if m]
        d["modifiers"] = [{"name": m.name, "type": m.type} for m in o.modifiers]
    if o.type == "LIGHT":
        d["light"] = {"type": o.data.type, "energy": o.data.energy, "color": list(o.data.color)}
    if o.type == "CAMERA":
        d["camera"] = {"lens_mm": o.data.lens, "active": bpy.context.scene.camera == o}
    return d


def _deselect_all():
    for o in bpy.context.view_layer.objects:
        o.select_set(False)


# ---------- operations ----------

def op_ping(p):
    return {
        "blender": bpy.app.version_string,
        "file": bpy.data.filepath or None,
        "background": bpy.app.background,
        "bridge": BRIDGE_VERSION,
    }


def op_scene_info(p):
    sc = bpy.context.scene
    return {
        "scene": sc.name,
        "file": bpy.data.filepath or None,
        "unit_system": sc.unit_settings.system,
        "frame_range": [sc.frame_start, sc.frame_end],
        "render": {
            "engine": sc.render.engine,
            "resolution": [sc.render.resolution_x, sc.render.resolution_y],
        },
        "camera": sc.camera.name if sc.camera else None,
        "objects": [_describe(o) for o in sc.objects],
        "materials": [m.name for m in bpy.data.materials],
    }


PRIMITIVES = {
    "cube": lambda: bpy.ops.mesh.primitive_cube_add(),
    "sphere": lambda: bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24),
    "ico_sphere": lambda: bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3),
    "cylinder": lambda: bpy.ops.mesh.primitive_cylinder_add(vertices=48),
    "cone": lambda: bpy.ops.mesh.primitive_cone_add(vertices=48),
    "torus": lambda: bpy.ops.mesh.primitive_torus_add(),
    "plane": lambda: bpy.ops.mesh.primitive_plane_add(),
    "monkey": lambda: bpy.ops.mesh.primitive_monkey_add(),
}


def _apply_transform(o, p):
    if p.get("location") is not None:
        o.location = _vec(p["location"])
    if p.get("rotation_deg") is not None:
        o.rotation_euler = [math.radians(x) for x in _vec(p["rotation_deg"])]
    if p.get("scale") is not None:
        s = p["scale"]
        o.scale = [float(s)] * 3 if isinstance(s, (int, float)) else _vec(s)


def _apply_scale(o):
    _deselect_all()
    o.select_set(True)
    bpy.context.view_layer.objects.active = o
    with bpy.context.temp_override(active_object=o, selected_editable_objects=[o], selected_objects=[o]):
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


def _apply_size(o, p):
    if p.get("dimensions") is not None:
        o.dimensions = _vec(p["dimensions"])
    if p.get("apply_scale") or p.get("dimensions") is not None:
        bpy.context.view_layer.update()
        _apply_scale(o)


def op_object_create(p):
    kind = p.get("primitive", "cube")
    if kind not in PRIMITIVES:
        raise ValueError("primitive must be one of " + ", ".join(sorted(PRIMITIVES)))
    _deselect_all()
    PRIMITIVES[kind]()
    o = bpy.context.active_object
    if p.get("name"):
        o.name = p["name"]
    _apply_transform(o, p)
    _apply_size(o, p)
    if p.get("smooth"):
        for poly in o.data.polygons:
            poly.use_smooth = True
    return _describe(o)


def op_object_edit(p):
    o = _obj(p["name"])
    if p.get("delete"):
        bpy.data.objects.remove(o, do_unlink=True)
        return {"deleted": p["name"]}
    _apply_transform(o, p)
    _apply_size(o, p)
    if "parent" in p:
        o.parent = _obj(p["parent"]) if p["parent"] else None
    if p.get("rename"):
        o.name = p["rename"]
    if "smooth" in p and o.type == "MESH":
        for poly in o.data.polygons:
            poly.use_smooth = bool(p["smooth"])
    return _describe(o)


def op_modifier_add(p):
    o = _obj(p["object"])
    mtype = p["type"].upper()
    m = o.modifiers.new(name=p.get("name") or mtype.title(), type=mtype)
    for k, v in (p.get("settings") or {}).items():
        if k == "object":
            v = _obj(v)
        if not hasattr(m, k):
            raise ValueError("%s modifier has no setting %r" % (mtype, k))
        setattr(m, k, v)
    return _describe(o)


def op_material_set(p):
    o = _obj(p["object"])
    name = p.get("material") or (o.name + " material")
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        raise ValueError("material %r has no Principled BSDF node" % name)
    fields = {
        "color": "Base Color",
        "metallic": "Metallic",
        "roughness": "Roughness",
        "emission_color": "Emission Color",
        "emission_strength": "Emission Strength",
        "alpha": "Alpha",
        "transmission": "Transmission Weight",
    }
    for key, socket_name in fields.items():
        if key in p and socket_name in bsdf.inputs:
            v = p[key]
            if key in ("color", "emission_color"):
                v = list(v) + [1.0] if len(v) == 3 else list(v)
            bsdf.inputs[socket_name].default_value = v
    if o.data.materials:
        o.data.materials[0] = mat
    else:
        o.data.materials.append(mat)
    return {"object": o.name, "material": mat.name}


def op_light_set(p):
    name = p.get("name") or "Light"
    o = bpy.data.objects.get(name)
    if o is None:
        data = bpy.data.lights.new(name, type=p.get("type", "AREA").upper())
        o = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(o)
    elif p.get("type"):
        o.data.type = p["type"].upper()
    if "energy" in p:
        o.data.energy = float(p["energy"])
    if "color" in p:
        o.data.color = _vec(p["color"])
    if "size" in p and hasattr(o.data, "size"):
        o.data.size = float(p["size"])
    _apply_transform(o, p)
    if p.get("look_at") is not None:
        _look_at(o, _vec(p["look_at"]))
    return _describe(o)


def _look_at(o, target):
    from mathutils import Vector
    direction = Vector(target) - o.location
    o.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def op_camera_set(p):
    name = p.get("name") or "Camera"
    o = bpy.data.objects.get(name)
    if o is None:
        o = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        bpy.context.scene.collection.objects.link(o)
    if "lens_mm" in p:
        o.data.lens = float(p["lens_mm"])
    _apply_transform(o, p)
    if p.get("look_at") is not None:
        _look_at(o, _vec(p["look_at"]))
    bpy.context.scene.camera = o
    return _describe(o)


def op_world_set(p):
    sc = bpy.context.scene
    world = sc.world or bpy.data.worlds.new("World")
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is None:
        raise ValueError("the world has no Background node")
    if "color" in p:
        c = list(p["color"])
        bg.inputs["Color"].default_value = c + [1.0] if len(c) == 3 else c
    if "strength" in p:
        bg.inputs["Strength"].default_value = float(p["strength"])
    vs = sc.view_settings
    if "view_transform" in p:
        vs.view_transform = p["view_transform"]
    if "look" in p:
        vs.look = p["look"]
    if "exposure" in p:
        vs.exposure = float(p["exposure"])
    return {
        "color": list(bg.inputs["Color"].default_value),
        "strength": bg.inputs["Strength"].default_value,
        "view_transform": vs.view_transform, "look": vs.look, "exposure": vs.exposure,
    }


def op_render(p):
    sc = bpy.context.scene
    engine = p.get("engine")
    if engine:
        sc.render.engine = {"eevee": "BLENDER_EEVEE_NEXT", "cycles": "CYCLES", "workbench": "BLENDER_WORKBENCH"}.get(engine.lower(), engine)
    if p.get("resolution"):
        sc.render.resolution_x, sc.render.resolution_y = [int(x) for x in p["resolution"]]
    sc.render.resolution_percentage = 100
    if p.get("samples"):
        if sc.render.engine == "CYCLES":
            sc.cycles.samples = int(p["samples"])
        elif hasattr(sc, "eevee"):
            sc.eevee.taa_render_samples = int(p["samples"])
    if sc.camera is None:
        raise ValueError("the scene has no camera — call camera_set first")
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = p["path"]
    bpy.ops.render.render(write_still=True)
    return {"path": p["path"], "engine": sc.render.engine, "resolution": [sc.render.resolution_x, sc.render.resolution_y]}


def op_viewport_shot(p):
    if bpy.app.background:
        raise ValueError("there is no viewport in background mode — use render")
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                with bpy.context.temp_override(window=window, area=area):
                    bpy.ops.screen.screenshot_area(filepath=p["path"])
                return {"path": p["path"]}
    raise ValueError("no 3D viewport is open")


EXPORTERS = {
    "glb": lambda path: bpy.ops.export_scene.gltf(filepath=path, export_format="GLB"),
    "gltf": lambda path: bpy.ops.export_scene.gltf(filepath=path, export_format="GLTF_SEPARATE"),
    "obj": lambda path: bpy.ops.wm.obj_export(filepath=path),
    "fbx": lambda path: bpy.ops.export_scene.fbx(filepath=path),
    "stl": lambda path: bpy.ops.wm.stl_export(filepath=path),
}


def op_export(p):
    fmt = p.get("format", "glb").lower()
    if fmt not in EXPORTERS:
        raise ValueError("format must be one of " + ", ".join(sorted(EXPORTERS)))
    EXPORTERS[fmt](p["path"])
    return {"path": p["path"], "format": fmt}


def op_save_file(p):
    if p.get("path"):
        bpy.ops.wm.save_as_mainfile(filepath=p["path"])
    elif bpy.data.filepath:
        bpy.ops.wm.save_mainfile()
    else:
        raise ValueError("the file was never saved — give a path")
    return {"file": bpy.data.filepath}


def op_new_scene(p):
    bpy.ops.wm.read_homefile(use_empty=bool(p.get("empty", True)))
    return op_scene_info({})


def op_run_python(p):
    env = {"bpy": bpy, "math": math, "result": None}
    out = io.StringIO()
    with redirect_stdout(out):
        exec(compile(p["code"], "<run_python>", "exec"), env)
    res = env.get("result")
    try:
        json.dumps(res)
    except TypeError:
        res = repr(res)
    return {"stdout": out.getvalue()[-20000:], "result": res}


def op_group(p):
    """An empty that parents the given objects, so a whole assembly moves,
    rotates and duplicates as one."""
    objs = [_obj(n) for n in p["objects"]]
    if p.get("location") is not None:
        loc = _vec(p["location"])
    else:
        xs = [o.matrix_world.translation for o in objs]
        loc = [sum(v[i] for v in xs) / len(xs) for i in range(2)] + [min(v[2] for v in xs)]
    empty = bpy.data.objects.new(p["name"], None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.location = loc
    bpy.context.scene.collection.objects.link(empty)
    bpy.context.view_layer.update()
    inv = empty.matrix_world.inverted()
    for o in objs:
        world = o.matrix_world.copy()
        o.parent = empty
        o.matrix_parent_inverse = inv
        o.matrix_world = world
    return {"group": empty.name, "location": [round(x, 4) for x in empty.location], "children": [o.name for o in objs]}


def _copy_tree(o, parent, suffix, linked):
    c = o.copy()
    if o.data is not None and not linked:
        c.data = o.data.copy()
    c.name = o.name + suffix
    for coll in o.users_collection:
        coll.objects.link(c)
    c.parent = parent if parent is not None else o.parent
    c.matrix_parent_inverse = o.matrix_parent_inverse.copy()
    for ch in o.children:
        _copy_tree(ch, c, suffix, linked)
    return c


def op_duplicate(p):
    """Copy an object with everything parented under it, count times, each
    copy moved by offset (and turned by rotation_deg) from the previous one."""
    src = _obj(p["name"])
    count = int(p.get("count", 1))
    if not 1 <= count <= 100:
        raise ValueError("count must be 1..100")
    offset = _vec(p.get("offset") or [0, 0, 0])
    turn = _vec(p.get("rotation_deg") or [0, 0, 0])
    names = p.get("names") or []
    made = []
    for i in range(1, count + 1):
        suffix = "_%d" % (i + 1)
        c = _copy_tree(src, None, suffix, bool(p.get("linked")))
        if i - 1 < len(names) and names[i - 1]:
            c.name = names[i - 1]
        c.location = [src.location[k] + offset[k] * i for k in range(3)]
        c.rotation_euler = [src.rotation_euler[k] + math.radians(turn[k]) * i for k in range(3)]
        made.append(c.name)
    bpy.context.view_layer.update()
    return {"copies": made}


BATCH_OPS = ("object_create", "object_edit", "modifier_add", "material_set", "light_set", "camera_set", "group", "duplicate", "world_set")


def op_batch(p):
    """Run many steps in one call, in order. Stops at the first failure and
    says which step failed; the steps before it stay done."""
    results = []
    for i, step in enumerate(p.get("steps") or []):
        name = step.get("op")
        if name not in BATCH_OPS:
            raise ValueError("step %d: op must be one of %s" % (i, ", ".join(BATCH_OPS)))
        try:
            r = OPS[name](step.get("params") or {})
        except Exception as e:
            raise ValueError("step %d (%s) failed after %d done: %s: %s" % (i, name, i, type(e).__name__, e))
        results.append({"op": name, "name": r.get("name") or r.get("group") or r.get("object"), "copies": r.get("copies")})
    return {"done": len(results), "steps": results}


OPS = {name[3:]: fn for name, fn in globals().items() if name.startswith("op_")}


# ---------- serving ----------

def _handle(req, token):
    rid = req.get("id")
    if token and req.get("token") != token:
        return {"id": rid, "ok": False, "error": "bad token"}
    fn = OPS.get(req.get("op"))
    if fn is None:
        return {"id": rid, "ok": False, "error": "unknown op %r" % req.get("op")}
    try:
        return {"id": rid, "ok": True, "result": fn(req.get("params") or {})}
    except Exception as e:  # the agent reads the reason and tries again
        return {"id": rid, "ok": False, "error": "%s: %s" % (type(e).__name__, e), "trace": traceback.format_exc()[-4000:]}


def _serve_conn(conn, token, run):
    with conn:
        f = conn.makefile("rwb")
        for line in f:
            try:
                req = json.loads(line)
            except ValueError:
                continue
            answer = run(req, token)
            f.write((json.dumps(answer) + "\n").encode())
            f.flush()


def _run_on_main(req, token):
    """From a socket thread: queue the request for the main thread and wait."""
    box = {}
    done = threading.Event()
    _requests.put((req, box, done))
    done.wait()
    return box["answer"]


def _drain(token):
    while True:
        try:
            req, box, done = _requests.get_nowait()
        except queue.Empty:
            return 0.05
        box["answer"] = _handle(req, token)
        done.set()


def start():
    port, token, port_file = _args()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Port 0: the system picks a free port. A fixed one can fall in a range
    # Windows reserves (Hyper-V/WSL), which refuses the bind.
    srv.bind((HOST, port))
    srv.listen()
    port = srv.getsockname()[1]
    if port_file:
        with open(port_file, "w") as f:
            f.write(str(port))
    print("picode-blender bridge on %s:%d" % (HOST, port), flush=True)
    if bpy.app.background:
        # No event loop: the main thread serves, one connection at a time.
        while True:
            conn, _ = srv.accept()
            _serve_conn(conn, token, _handle)
    else:
        def accept_loop():
            while True:
                conn, _ = srv.accept()
                threading.Thread(target=_serve_conn, args=(conn, token, _run_on_main), daemon=True).start()
        threading.Thread(target=accept_loop, daemon=True).start()
        bpy.app.timers.register(lambda: _drain(token), persistent=True)


start()
