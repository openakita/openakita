---
name: cli-anything
description: Control desktop software (GIMP, Blender, LibreOffice, etc.) through CLI-Anything generated command-line interfaces. Calls real application backends — much more reliable than GUI automation via desktop_* tools.
system: true
handler: cli_anything
tool-name: cli_anything_run
category: Desktop
priority: high
---

# CLI-Anything - CLI

Via [CLI-Anything](https://github.com/HKUDS/CLI-Anything) Generation CLI. 
Call API, pyautogui/UIA GUI Automatic. 

## Core

- **Call** — GIMP, LibreOffice Generation PDF
- ** JSON ** — `--json` and `--help` Supports
- ** GUI Automatic 100x** — not, 

## When to Use CLI-Anything ( desktop_* ) 

| | Recommendations | |
|------|---------|------|
| have CLI | `cli_anything_run` |, JSON |
| CLI | `desktop_*` | GUI Automatic |
| View | `cli_anything_discover` | PATH |
| | `cli_anything_help` | Get --help |

## Tool

### cli_anything_discover —

```python
cli_anything_discover()
```

### cli_anything_run — Execute

```python
cli_anything_run(app="gimp", subcommand="image resize", args=["--width", "800", "input.png"])
cli_anything_run(app="libreoffice", subcommand="document export-pdf", args=["report.docx"])
```

### cli_anything_help — View

```python
cli_anything_help(app="gimp")
cli_anything_help(app="gimp", subcommand="image resize")
```

## Installation CLI-Anything

```bash
# CLI-Hub have CLI
pip install cli-anything-gimp
pip install cli-anything-blender
pip install cli-anything-libreoffice

# Generation CLI (need Claude Code) 
/cli-anything./your-software
```

## Supports

CLI-Anything Supports 9+: 

- ****: GIMP, Blender, Inkscape, Audacity, OBS Studio
- ****: LibreOffice
- **AI **: Stable Diffusion, ComfyUI
- ****: Jenkins, Gitea, pgAdmin

##

```
need? 
├─ have cli-anything CLI → cli_anything_run () 
├─ CLI + Windows → desktop_* (GUI Automatic) 
└─ CLI + Windows → run_shell
```

## Related

- `desktop_click` / `desktop_type` — CLI GUI
- `run_shell` — Execute