# Changelog

## 1.0.0 — 2026-10-27

First release.

- `ilx_w650.py` command-line tool: `info`, `extract`, `wallpaper`, `verify`.
- Extraction uses the image's own boot-loader unpacker under emulation (no external RAR tool).
- RAR 2.0 compressor for rebuilding the main archive.
- Verification with the unit's own code under emulation: pre_main's unpacker and `Show_BG_Image`.
- `web/wallpaper_patcher.html`: offline, single-file browser version with its own RAR 2.0 decoder/encoder.
- Documentation of the flash layout, boot chain, update/recovery logic and wallpaper format.
- Tested on hardware: black wallpaper flashed to an iLX-W650 (firmware `V0.005-r01`).
