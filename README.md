# VPX Launcher (Simple)

A lightweight desktop launcher for Visual Pinball X tables with fast search, A-Z jump navigation, wheel-image preview, and wheel-media auto-download.

## Features

- Recursively scans a folder for `.vpx` tables
- Live search and quick A-Z jump bar
- Launches selected tables in VPX
- Shows local wheel preview (`medias/wheel.png` or similar)
- Scans for missing wheel media
- Fuzzy-matches table names using VPS DB metadata
- Supports manual match override before downloading wheel art
- Caches DB files locally for faster future scans
- Remembers your last selected tables folder
- Works on macOS and Linux

## Requirements

- Python 3.10+
- `tkinter` (usually included with system Python)
- Visual Pinball X executable:
  - macOS: `/Applications/VPinballX_BGFX.app/Contents/MacOS/VPinballX_BGFX`
  - Linux: `VPinballX_BGFX` in one of:
    - `~/vpinball/VPinballX_BGFX`
    - `~/VPX/VPinballX_BGFX`
    - `/usr/local/bin/VPinballX_BGFX`
    - `/opt/vpinball/VPinballX_BGFX`
    - or available on `PATH`
- Optional on Linux for non-PNG previews: ImageMagick `convert`

## Run

```bash
python3 VPX-Launcher-simple.py
```

## Typical Workflow

1. Click **Select Folder** and choose your VPX tables folder.
2. Use search or the A-Z bar to find a table quickly.
3. Double-click a table (or press **Launch**) to start it.
4. Click **Scan Media** to find tables missing wheel images.
5. Review auto-matches, optionally use **Manual Search**, then **Apply**.
6. Click **Update DB** anytime to refresh local metadata caches.

## Data Sources

- `vpinmdb.json` from `superhac/vpinmediadb`
- `vpsdb.json` from `VirtualPinballSpreadsheet/vps-db`

## Local Files Used

- `~/.vpx_launcher_config.json` (last selected folder)
- `~/.vpx_vpinmdb.json` (cached media DB)
- `~/.vpx_vpsdb.json` (cached VPS DB)

## Notes

- Matching quality depends on table filename quality.
- Default fuzzy threshold is `0.45` in the script (`FUZZY_MIN`).
- If the app cannot launch VPX on macOS, confirm the VPX app path exists.
