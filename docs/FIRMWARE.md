# iLX-W650 firmware internals

Everything here was worked out by static analysis of the update file and by running parts of the
firmware in an emulator. Addresses are offsets in the file named, unless stated otherwise.
Statements marked *(unverified)* are informed guesses.

## Hardware and software

- SoC: Telechips **TMM9200** family, ARM926 core running **big-endian** (BE32), code mostly Thumb with
  some ARM. Build paths show Telechips' `TMM9200_SDK_V2.76` and its RTOS `iRTOS V1.1006`.
- Storage: one **8 MiB serial (SPI) flash**, mapped by the SoC at `0x20000000`.
- A separate system microcontroller ("micom", STM32F0-class Cortex-M0) handles ignition power,
  battery monitoring, backlight/dimmer, reversing-camera switching and IR/steering keys; it talks to
  the main SoC over a serial link and has its own update file (`micom.srec`).

## The update file is a flash image

`Alpine_SYSTEM.FIR` is exactly 8,388,608 bytes: a raw image of the serial flash.

| Offset | Contents |
|---|---|
| `0x000000` | Stage-1 boot loader (runs in place from flash at `0x20000000`): clocks, SDRAM set-up |
| `0x000C00`–`0x001FD8` | Stage-2 boot loader, copied to RAM at `0x01F00000`; contains a RAR unpacker |
| `0x001FFC` | 4-byte big-endian length of the next archive |
| `0x002000` | RAR archive: `recovery_16.bin` (395,408 bytes unpacked) |
| `0x040000` | RAR archive: `pre_main_16.bin` (796,064 bytes) |
| `0x0CD155`–`0x17FFFB` | erased (`0xFF`) |
| `0x180000` | RAR archive: `main_16.bin` (9,499,836 bytes) — the main application |
| …–`0x7EFFFF` | erased; available to a larger main archive |
| `0x7FFFC0` | footer tag `ALPINE_ILX_SYS`, then `"005"` (version field) |
| `0x7FFFE0` | marker `5A 5A 5A 5A A5 A5 A5 A5` |
| `0x7FFFF0` | **checksum**: 32-bit sum of every byte from `0x000000` to `0x7FFFEF` (big-endian) |

Each archive is preceded by its total length (4 bytes, big-endian) and is a single-file archive
in the RAR 1.5–4.x container: 7-byte marker, 13-byte main header, file header, compressed data, no
end block. The data uses **RAR 2.0 compression** (unpack version 20, method `0x33`, 1 MB
dictionary). Note that macOS `bsdtar`/libarchive silently produces corrupt output for this format
and Homebrew's 7-Zip has no RAR decoder; `unar` decodes it correctly, as do this project's tools.

### Footer checksum

```
sum = (bytes[0x000000] + … + bytes[0x7FFFEF]) mod 2^32   →   stored big-endian at 0x7FFFF0
```

Both updaters compute it by summing bytes until they reach the `5A5A5A5A A5A5A5A5` marker and
comparing with the following word, which is the same thing.

### File type tag

The footer tag selects how an updater writes the file:

| Tag | Written from | Notes |
|---|---|---|
| `ALPINE_ILX_ALL` | `0x000000` | whole flash including boot loader |
| `ALPINE_ILX_SYS` | `0x180000` | main archive + footer only (the normal update) |
| `ALPINE_ILX_PRE` | `0x180000` | |
| `ALPINE_ILX_REC` | `0x000000` | whole flash |

A `_SYS` update therefore **cannot** change the boot loader, recovery or `pre_main`.

## Boot chain

1. **Stage-1** (flash `0x0`) initialises clocks and SDRAM and copies stage 2 to `0x01F00000`.
2. **Stage-2** (`0x01F002BC`) checks two markers (`0x01F00264`):
   - the footer at `0x7FFFC0` starts with `ALPINE_ILX`, and
   - flash `0x180000` starts with `Rar`.

   Both present → unpack **`pre_main`** from `0x40000` to `0x01000000` and run it.
   Otherwise → unpack **`recovery`** from `0x2000` to `0` and run it.
   It does **not** checksum the main firmware.
3. **pre_main** (base `0x01000000`, Thumb) shows the boot logo and unpacks `main_16.bin` from
   `0x180000` to address `0` with its own unpacker (`0x01004C60`), then jumps to it. Two properties
   matter for safety:
   - it **never checks the RAR CRC** — a corrupted stream "succeeds" and jumps into garbage;
   - if the archive header does not parse, it **retries forever** — there is no fallback to recovery.
4. **main** (loaded at `0`) is the application: UI, CarPlay (Apple AirPlay receiver), Android Auto,
   CarLife, Bluetooth, tuner, media.

The stage-2 unpacker (`0x01F0106C`) and pre_main's unpacker take `(dest, &size_out, src)`;
`ilxw650/emu.py` calls both under emulation.

## Updating

There are two updaters, with the same logic:

- **main firmware's** own updater (Firmware Upgrade screen; driver `0x92568`, ARM-mode file
  handling at `0x21000`–`0x23A00`), which flashes directly — it does not reboot into recovery;
- **recovery** (`recovery_16.bin`), which only runs when the boot markers are missing.

Both look for files in a **folder named after the model**, chosen from a model code the unit stores
internally:

| Model code | Folder |
|---|---|
| `0x10` | `iLX-206` |
| `0x20` | `iLX-206A` (also the default) |
| `0x30` | `iLX-206E` |
| `0x40` | **`iLX-W650`** |

Files the main updater knows (by internal "kind"):

| Kind | File | Updates |
|---|---|---|
| 1 | `Alpine_SYSTEM.FIR` | serial flash (this image) |
| 2 | `micom.srec` | system microcontroller (Motorola S-record, app at `0x0800A000`) |
| 3 | `tw8_mcu.bin` + `tw8_img.bin` | TW88xx video chip firmware and image data |
| 4 | `Alpine_BT.dfu` | Bluetooth module |
| 5 | `OPENINGFILE\openingfile.jpg` | custom opening image — **disabled in this build**, see below |

The media are tried as USB 2.0, USB 1.1 and "FX" *(probably the SD slot — unverified)*.

Flashing a `.FIR` (both updaters):

1. Read the whole file into RAM; reject it unless it is exactly the flash size.
2. Check the byte-sum checksum (re-read up to 5 times).
3. Classify by footer tag (table above).
4. **Erase** from the start offset to the end of flash — the footer is erased first.
5. Write, then read back and compare every word (main: 2 attempts).
6. Reboot.

Consequences:

- **No version, model or downgrade checks** (beyond the folder name). The `"005"` field is never read.
- A power cut during steps 4–5 leaves no footer tag → next boot goes to **recovery** → retry from USB.
- A `.FIR` that writes successfully but whose main firmware does not start leaves a valid footer →
  pre_main loops or crashes, recovery never runs → only an SPI programmer can restore the flash.

## Custom opening screen (disabled)

The firmware contains Alpine's "Opening Customize" feature: import `OPENINGFILE\openingfile.jpg`
(also `openingfile.JPG`, `Openingfile.jpg`; JFIF/Exif JPEG < 1 MB), store it at flash `0x080000`
as `[size][JPEG]`, show it at boot; service menu "13. Erase Opening Screen" erases 64 KB there.

In this build it is cut off: the import reads the file but never writes it, and the load
(`0x19F11E`), erase (`0x46E9A`) and validation functions have no callers. On this model
`0x080000` lies inside the `pre_main` archive, so enabling it would break booting — do not.
The opening image actually shown is a built-in JPEG (`main` `0x384A48`, 356,160-byte slot; `pre_main`
has its own copy at `0x48A24`).

## Main firmware facts

| Item | Value |
|---|---|
| Version string | `V0.005-r01` (`main` `0x17ECC`) |
| Load address | `0` (pre_main unpacks it there) |
| Background routine | `Show_BG_Image` at `0x7ACEA` — see [WALLPAPER.md](WALLPAPER.md) |
| Updater driver | `0x92568` (Thumb), flash writer `0x46BA4`, file handling ARM `0x22760` |
| UI text resources | table around `0x3FD560` (English; other languages elsewhere) |

## Emulation notes

`ilxw650/emu.py` runs the unpackers and `Show_BG_Image` in [Unicorn](https://www.unicorn-engine.org/)
(`UC_MODE_ARM | UC_MODE_BIG_ENDIAN`, CPU model ARM926). Things that matter:

- Keep Unicorn's default CPSR **E bit** (`0x200`) — clearing it makes data accesses little-endian.
- Unicorn's `mrc p15, c1` returns 0, so letting the firmware write SCTLR back drops big-endian
  mode; ignore those writes (flat memory needs no MMU anyway).
- main's C library calls **ARM semihosting** (`SVC 0xAB` Thumb / `0x123456` ARM) at start-up.
- Booting main from reset (`research/boot_main.py`) currently stops at the clock controller
  (`0x3C500000`). A full emulator would also need the interrupt controller (`0x39C00000`), timer
  (`0x3CE00000`), serial-flash controller (`0x38200000`), I2C devices (touch, TW88xx, PMIC), the
  micom serial protocol, and the display/2D graphics hardware.
