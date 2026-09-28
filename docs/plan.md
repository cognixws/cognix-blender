# picode-blender — plan

Status: v1 in progress (2026-09-28). Written from scratch; other projects
(GodotForge's Blender add-on, blender-mcp and similar) are references only —
no code is copied.

## What it is

A PiCode extension (ADR-0230, slice E0 shape): an MCP server and skills that
PiCode hands to agents started in a workspace where the extension is on.

```
picode-extension.json   manifest
server/                 MCP server over stdio, Python standard library only
blender/                bridge script Blender loads at start: a local socket inside Blender
skills/                 how to model well through the tools
docs/                   this plan, the tool reference, decisions
```

## How it works

1. An agent calls `blender_open`: the server starts Blender (the Windows
   `blender.exe` from WSL, or the local `blender`) with `--python
   blender/bridge.py`. Nothing is installed into Blender permanently.
2. The bridge listens on `127.0.0.1` inside Blender and runs each request on
   Blender's main thread (a timer drains a queue; `bpy` is not thread-safe).
3. Every other tool sends one JSON request to the bridge and returns its
   answer. A render returns the image so the agent can look at it.
4. PiCode's computer-use tools complement this: the agent can look at
   Blender's interface and viewport when a check needs the real window.

## Tools (v1)

| Tool | Does |
|---|---|
| `blender_open` | start Blender with the bridge (or report it is already up) |
| `blender_status` | is the bridge reachable; Blender version; open file |
| `scene_info` | objects, cameras, lights, materials, frame range, units |
| `object_create` | primitive (cube, sphere, cylinder, cone, torus, plane, monkey), name, location, rotation, scale |
| `object_edit` | move / rotate / scale / rename / parent / delete |
| `modifier_add` | add a modifier with settings (bevel, subdivision, array, mirror, solidify, boolean…) |
| `material_set` | create or update a Principled BSDF material and assign it |
| `light_set`, `camera_set` | add or change a light; place and aim the camera |
| `render` | render the scene (Eevee or Cycles, size, samples) to a PNG and return it |
| `viewport_shot` | capture the 3D viewport as an image |
| `export` | glTF / GLB, OBJ, FBX, STL to a path |
| `save_file` | save the .blend |
| `run_python` | run Python inside Blender: the door for anything without a tool |

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Own public repository, installed from its git URL | ADR-0230 D1: distribution by git first |
| D2 | Python standard library for the server | no install step; Blender is Python too |
| D3 | Bridge loaded at start, not an installed add-on | nothing left behind in the owner's Blender |
| D4 | `run_python` in v1 | capability first; agents act with the owner's permissions |
| D5 | WSL: mirrored networking is this extension's requirement, not PiCode's | only here does a WSL process reach a Windows app on localhost |

## Roadmap

1. Bridge + server + open/status/scene_info/run_python; live test from PiCode.
2. The modeling tools, render and viewport capture; skills.
3. A demo scene built end to end by an agent, with computer use checking the window.
4. Marketing video (HyperFrames) from real captures of PiCode + Blender.
