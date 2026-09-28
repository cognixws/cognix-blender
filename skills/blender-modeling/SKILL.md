---
name: blender-modeling
description: Build 3D scenes in Blender with the blender MCP tools — open Blender, model with primitives and modifiers, light, frame a camera, render and check the image, iterate, then save or export.
---

# Modeling in Blender through the tools

## Loop

1. `blender_open` (a window the owner can watch; `headless: true` only for batch work).
2. `scene_info` before changing anything; `new_scene` for a clean start.
3. Block out with `object_create` at real-world scale (meters, Z up, floor at z=0).
   Size things with `dimensions`, not `scale`: it is applied to the mesh, so a
   bevel or a solidify keeps its real width.
4. Refine with `modifier_add`: BEVEL for edges that catch light, SUBSURF for
   organic shapes (with `smooth: true`), ARRAY/MIRROR for repetition, SOLIDIFY
   for thickness, BOOLEAN to cut.
5. `material_set` on every visible object: color in linear RGB 0–1, roughness
   0.2–0.6 for most surfaces, metallic 0 or 1.
6. Light: a key AREA light (500–1500 W) aimed with `look_at`, a weaker fill on
   the other side, and a rim light behind for separation.
7. `camera_set` with `look_at` on the subject; 35–50 mm for objects, 24 mm for rooms.
   Keep what matters inside the frame (a bevel you cannot see is wasted).
   `world_set` for the background color and exposure: a washed-out render
   usually wants less light energy or `exposure` below 0, not a new camera.
8. `render` (eevee while iterating, cycles for the final) and **look at the
   image** before saying it is done. Fix what reads wrong and render again.
9. `save_file` and, if asked, `export` (glb for the web and game engines).

## Many parts

A scene with more than a handful of parts goes through `build`: one call with
all the steps. Build one assembly (a desk, its chair, its monitor), `group` it,
then `duplicate` the group with an `offset` — four workstations are one
`build` call, not a hundred tool calls or a `run_python` script.

## Rules

- One change at a time, then check: a render or `scene_info`.
- Name every object (`name`) so later calls can refer to it.
- Anything without a tool — bmesh edits, curves, geometry nodes, animation
  keys — goes through `run_python`; assign `result` to see a value back.
- In PiCode, the computer tools can look at Blender's window when the viewport
  itself matters; `viewport_shot` is the quicker way to see the 3D view.
