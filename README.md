# picode-blender

A [PiCode](https://github.com/cfpperche/picode) extension that lets your agents
model in Blender: they build the scene through tools, render it, look at the
result, and you watch it happen in Blender's own window.

> v0.4 — tools in [docs/tools.md](docs/tools.md), plan in [docs/plan.md](docs/plan.md).

## Install

In PiCode: **Extensions → Install extension**, paste
`https://github.com/cfpperche/picode-blender`, review, **Install**, then turn it
on in the workspaces where you want it.

## In PiCode

- **Agents** get the Blender tools and the modeling skill in the workspaces
  where the extension is on.
- **The Blender scene page** (Apps → Blender, or its tab) shows whether
  Blender is open, the file, the objects, and a snapshot of the 3D viewport.
- **Open with**: a `.blend` file in PiCode's file view offers **Open in
  Blender**; the page then opens that file in Blender, and never replaces a
  scene that is already open.
- **Menus**: the workspace menu has *Open the Blender scene* and *Take a
  Blender snapshot*; the agent menu has *Take a Blender snapshot*. Both are
  also in the command palette.
- **Turns itself on** in a workspace whose folder has a `.blend` file,
  unless you turned it off there.

A small background program (`server/picode_blender_page.py`, standard
library only) answers the page and the menu commands; PiCode starts it while
the extension is on in a workspace.

## Requirements

- PiCode with extensions (ADR-0230).
- Blender 4.2 or newer.
- Python 3.10+ on the machine running PiCode.
- On Windows with PiCode inside WSL: WSL **mirrored** networking, so the
  extension (in WSL) can reach Blender (on Windows) at `localhost`.

## License

Apache-2.0.
