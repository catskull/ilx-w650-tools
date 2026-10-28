# ilx-w650-tools

Tools and documentation for the firmware of the **Alpine iLX-W650** car head unit — in particular,
replacing the home-screen wallpaper by patching the official `Alpine_SYSTEM.FIR` update file.

- **Web tool** — [`web/wallpaper_patcher.html`](web/wallpaper_patcher.html): a single offline page.
  Load your `Alpine_SYSTEM.FIR`, pick an image or colour, download a patched `Alpine_SYSTEM.FIR`.
  Nothing is uploaded.
- **Command line** — [`ilx_w650.py`](ilx_w650.py): inspect, extract, patch and verify images. Every
  patched image is checked by running the head unit's *own* decompressor and drawing code in an
  ARM emulator before it is written.
- **Documentation** — how the flash image, boot chain and update process work
  ([docs/](docs/)), and the reverse-engineering helpers used to find out ([research/](research/)).

> **Status:** a black wallpaper built with this pipeline has been flashed to a real iLX-W650 and
> works. Other images use the identical path, but this is still an unofficial modification:
> **read [docs/UPDATING.md](docs/UPDATING.md) before flashing anything.**

> **How this was made:** this project was entirely vibe coded — the reverse engineering, tools
> and documentation were produced with an AI coding assistant, not written by hand. It has been
> proven to work on my own iLX-W650, but it has not been independently reviewed, so treat the
> code and the findings in the docs accordingly.

## ⚠️ Read this first

- This is **unofficial** and **not affiliated with or endorsed by Alpine**. Modifying firmware may
  void your warranty. You use it entirely at your own risk.
- A firmware update that is interrupted is recoverable (the unit falls back to its recovery mode).
  But a main firmware that installs and then fails to start **cannot** be recovered over USB — only
  by reprogramming the unit's serial flash chip with an SPI programmer. The tools exist to make
  that case extremely unlikely: they only change wallpaper pixel data, and verify the result with
  the unit's own code. See [docs/VERIFICATION.md](docs/VERIFICATION.md).
- **No Alpine firmware is included.** You need your own copy of the iLX-W650 firmware update file
  `Alpine_SYSTEM.FIR` (it is sometimes passed around renamed, e.g. as `Alpine_SYSTEM.rar` — the
  contents are the same 8 MiB image).
- Only one firmware build is supported (main firmware `V0.005-r01`). Other builds are detected and
  refused rather than patched at possibly wrong offsets.

## Quick start: web tool

1. Open `web/wallpaper_patcher.html` in a current browser (double-clicking the file is fine).
2. **Firmware:** choose your `Alpine_SYSTEM.FIR`. The page checks it and shows the current wallpaper.
3. **New wallpaper:** choose an image (fill/crop, fit inside, or stretch to 800×480) or a solid colour.
4. **Build**, check the previews, then **Download Alpine_SYSTEM.FIR**.
5. Install it as described in [docs/UPDATING.md](docs/UPDATING.md).

The page rebuilds the archive and checksums and decompresses its own output to confirm it before
offering the download. For the strongest check, also run `python ilx_w650.py verify` on the result.

## Quick start: command line

Requires Python 3.9 or newer (developed and tested with 3.15).

```sh
python -m pip install -r requirements.txt          # numpy, pillow, unicorn

# Check an image and show its layout
python ilx_w650.py info Alpine_SYSTEM.FIR --deep

# Build a patched image in USB layout: out/iLX-W650/Alpine_SYSTEM.FIR (+ preview PNGs)
python ilx_w650.py wallpaper Alpine_SYSTEM.FIR my_picture.jpg -o out --usb
python ilx_w650.py wallpaper Alpine_SYSTEM.FIR --color '#000000' -o out --usb   # solid colour

# Re-run every safety check on any image (optionally against the stock one)
python ilx_w650.py verify out/iLX-W650/Alpine_SYSTEM.FIR --reference Alpine_SYSTEM.FIR

# Extract recovery/pre_main/main and the current wallpaper as PNGs
python ilx_w650.py extract Alpine_SYSTEM.FIR -o extracted
```

`wallpaper` options: `--fit cover|contain|stretch` (default `cover`: fill the screen and crop),
`--background '#RRGGBB'` (padding/transparency colour). The dimmed copy of the wallpaper (shown
under menus) is generated automatically at 30 % brightness, matching the stock one.

## Installing on the head unit (summary)

1. FAT32 USB stick (MBR partition table).
2. Folder **`iLX-W650`** in its root, containing only the patched `Alpine_SYSTEM.FIR`.
3. On the unit run **Firmware Upgrade**; keep power stable until it restarts.
4. To go back, flash your original `Alpine_SYSTEM.FIR` the same way (there is no version check).

Details, failure modes and recovery: [docs/UPDATING.md](docs/UPDATING.md).

## How it works

`Alpine_SYSTEM.FIR` is a raw image of the unit's 8 MiB serial flash: a boot loader, three RAR 2.0
archives (`recovery_16.bin`, `pre_main_16.bin`, `main_16.bin`) and a footer with a byte-sum
checksum. The wallpaper lives inside `main_16.bin` as raw interlaced NV12 video frames (a normal
and a dimmed copy) that the firmware copies straight into the display buffer.

Patching means: extract `main_16.bin` → overwrite the four wallpaper fields → recompress with a
purpose-written RAR 2.0 compressor → rebuild the image, keeping everything outside the main
archive byte-identical → fix the length prefix and footer checksum → verify.

- [docs/FIRMWARE.md](docs/FIRMWARE.md) — flash layout, boot chain, update and recovery behaviour
- [docs/WALLPAPER.md](docs/WALLPAPER.md) — wallpaper storage format and how the firmware draws it
- [docs/UPDATING.md](docs/UPDATING.md) — preparing the USB stick, flashing, restoring, de-bricking
- [docs/VERIFICATION.md](docs/VERIFICATION.md) — what is checked, and why
- [research/README.md](research/README.md) — disassembly/cross-reference helpers, emulation notes

## Repository layout

```
ilx_w650.py            command-line tool
ilxw650/               Python package: layout, firmware (parse/rebuild), rar20 (compressor),
                       wallpaper (NV12 conversion), emu (Unicorn harnesses)
web/                   wallpaper_patcher.html (built) + src/ + build.py + test/
docs/                  documentation
research/              reverse-engineering helpers
tests/                 pytest suite (runs against your own firmware file)
```

## Development

```sh
python -m pip install -r requirements-dev.txt
ILX_W650_FIR=/path/to/Alpine_SYSTEM.FIR python -m pytest      # skipped without a firmware file
node web/test/test_core.js /path/to/Alpine_SYSTEM.FIR           # web core on its own
python web/build.py                                             # rebuild web/wallpaper_patcher.html after editing web/src
```

The web tool's logic lives in `web/src/fir_core.js` (RAR 2.0 decoder and encoder, patcher, image
rebuild); the test suite checks its output with the Python emulators.

## License

Released into the public domain under [the Unlicense](LICENSE).

"Alpine" and "iLX-W650" are trademarks of their owner and are used here only to identify the
hardware these tools work with.
