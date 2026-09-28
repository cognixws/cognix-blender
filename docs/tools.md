# Tools

The MCP server is named `blender`. Every tool except `blender_open` needs a
Blender opened by `blender_open`.

| Tool | Arguments | Returns |
|---|---|---|
| `blender_open` | `headless`, `file` | Blender version, open file, `already_open` |
| `blender_close` | – | `closed` |
| `blender_status` | – | version and file, or `open: false` |
| `scene_info` | – | objects with transforms, dimensions, materials, modifiers; lights; cameras; render settings |
| `new_scene` | `empty` (default true) | the new scene |
| `object_create` | `primitive` (cube, sphere, ico_sphere, cylinder, cone, torus, plane, monkey), `name`, `location`, `rotation_deg`, `scale`, `smooth` | the object |
| `object_edit` | `name`, transform, `rename`, `parent`, `smooth`, `delete` | the object |
| `modifier_add` | `object`, `type`, `name`, `settings` (the modifier's Blender properties) | the object |
| `material_set` | `object`, `material`, `color`, `metallic`, `roughness`, `transmission`, `alpha`, `emission_color`, `emission_strength` | object and material |
| `light_set` | `name`, `type`, `energy`, `color`, `size`, `look_at`, transform | the light |
| `camera_set` | `name`, `lens_mm`, `look_at`, transform | the camera (made the scene camera) |
| `render` | `engine` (eevee, cycles, workbench), `resolution`, `samples`, `path` | the PNG as an image, and its path |
| `viewport_shot` | – | the 3D viewport as an image (window mode only) |
| `export` | `format` (glb, gltf, obj, fbx, stl), `path` | the path |
| `save_file` | `path` | the .blend path |
| `run_python` | `code` | `stdout` and `result` |

Files the tools write default to `~/.picode-blender/out/`. On WSL, paths are
translated for the Windows Blender with `wslpath`.

## How the bridge talks

`blender_open` starts Blender with `--python blender/bridge.py -- --port 0
--token <t> --port-file <f>`. The bridge binds `127.0.0.1` on a port the
system picks (a fixed port can fall in a range Windows reserves), writes it to
the port file, and serves one JSON request per line, run on Blender's main
thread. The server keeps the port and token in `~/.picode-blender/bridge.json`
(0600), so every agent's server reaches the same Blender.
