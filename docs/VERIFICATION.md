# Verification

## Why it matters

`pre_main` unpacks the main firmware at every boot **without checking the RAR CRC**, and recovery
only runs if the flash footer is invalid. So a patched image with a subtly wrong archive would pass
the unit's own update checks, install, and then fail to boot in a way USB cannot fix. The tools
therefore check their output against the unit's own code before you ever see a file.

## Checks

| Check | CLI `wallpaper` / `verify` | Web tool |
|---|---|---|
| Input is an 8 MiB `ALPINE_ILX_SYS` image with a valid footer checksum | ✓ | ✓ |
| All three archives parse, header CRCs valid, length prefixes consistent | ✓ | main |
| `main_16.bin` is the supported build (CRC32 of it with the wallpaper planes zeroed = `0x2B5C8407`) | ✓ | ✓ |
| Only the main slot and the footer checksum differ from the input | ✓ | ✓ |
| Rebuilt archive decompresses to exactly the patched `main_16.bin` (RAR CRC + byte compare) | ✓ (device code) | ✓ (own decoder) |
| **Device unpacker**: `pre_main`'s own unpacker, run in an ARM emulator, outputs exactly the patched `main_16.bin` | ✓ | — |
| **Device renderer**: `Show_BG_Image(3)` and `(8)`, run in the emulator, draw the new normal/dimmed copies | ✓ | — |
| Footer checksum recomputed and valid | ✓ | ✓ |

"Device code" means the routines are taken from the image itself (`pre_main_16.bin` and the
stage-2 boot loader are extracted from the very file being checked) and executed in
[Unicorn](https://www.unicorn-engine.org/); nothing is re-implemented for these checks.

## What is not verified

- Anything after the background is handed to the display hardware, and the rest of the running
  system. The patch changes only pixel data that the firmware copies verbatim, and the code is
  byte-identical, so no behavioural change is expected — but the real unit is the final test.
- The exact appearance on the panel (colour handling of the display pipeline).

## Test suite

`tests/test_pipeline.py` (pytest) runs against your own stock image:

```sh
ILX_W650_FIR=/path/to/Alpine_SYSTEM.FIR python -m pytest
```

It covers extraction with the boot loader, rebuilding unmodified firmware (round trip through the
device unpacker), a demonstration that the device unpacker accepts corrupted data, black-wallpaper
equivalence with the firmware's built-in black, an end-to-end `wallpaper` run, and — if Node is
installed — that the web tool's core produces an image passing the device emulation.
