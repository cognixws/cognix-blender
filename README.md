# picode-blender

A [PiCode](https://github.com/cfpperche/picode) extension that lets your agents
model in Blender: they build the scene through tools, render it, look at the
result, and you watch it happen in Blender's own window.

> Work in progress — see [docs/plan.md](docs/plan.md).

## Install

In PiCode: **Extensions → Install extension**, paste
`https://github.com/cfpperche/picode-blender`, review, **Install**, then turn it
on in the workspaces where you want it.

## Requirements

- PiCode with extensions (ADR-0230).
- Blender 4.2 or newer.
- Python 3.10+ on the machine running PiCode.
- On Windows with PiCode inside WSL: WSL **mirrored** networking, so the
  extension (in WSL) can reach Blender (on Windows) at `localhost`.

## License

Apache-2.0.
