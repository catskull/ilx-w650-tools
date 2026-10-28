# Research helpers

Small tools used to work out the firmware. They need `capstone` and/or `unicorn`
(`pip install -r requirements-dev.txt`). Extract the binaries first:

```sh
python ilx_w650.py extract Alpine_SYSTEM.FIR -o extracted
```

## disasm.py — annotated disassembly

```sh
python research/disasm.py <file> <load base> <start> <end> [arm|thumb]     # hex
python research/disasm.py extracted/main_16.bin 0 7acea 7ad90 thumb
```

Resolves PC-relative literal loads and `adr`/`add pc` targets, and prints ASCII or UTF-16BE strings
they point to (the updater stores filenames as UTF-16BE).

Load bases: `main_16.bin` → `0`; `pre_main_16.bin` → `1000000`; `recovery_16.bin` → `0`;
flash stage 2 (file `0xC00`) → base `1eff400` so that file `0xC00` maps to `0x01F00000`.

## xref.py — who uses this address?

```sh
python research/xref.py extracted/main_16.bin 0 637cb8 7acea
```

Finds literal-pool words, Thumb `ldr`/`adr`, ARM `add pc`/`ldr pc-relative`, and Thumb/ARM
`bl`/`blx` calls. `main_16.bin` mixes Thumb and ARM; the USB updater (`0x21000`–`0x23A00`) is ARM and
reaches Thumb code with ARM `blx`, so a Thumb-only scan wrongly suggests some functions are unused.

## boot_main.py — booting main from reset (experimental)

```sh
python research/boot_main.py extracted/main_16.bin 10
```

Runs `main_16.bin` from its reset vector with all peripheral space as logged, zero-reading MMIO,
handling the Unicorn quirks and semihosting described in [docs/FIRMWARE.md](../docs/FIRMWARE.md#emulation-notes).
It reaches the C runtime and exits with "Divide By Zero" at the first clock-controller read. It is a
starting point for anyone who wants to build a fuller emulator, not a working one.

## Useful addresses

| Where | Address | What |
|---|---|---|
| flash | `0x01F00264` (stage 2) | boot decision: footer tag + `Rar` marker |
| flash | `0x01F0106C` (stage 2) | boot loader RAR unpacker `(dest, &size, src)` |
| pre_main | `0x01002EA0` | pre_main main(): init, then unpack-main retry loop |
| pre_main | `0x01004C60` | unpack main (Thumb) `(dest, &size, src)` |
| main | `0x7ACEA` | `Show_BG_Image(type)` |
| main | `0x92568` | "Enter in Firmware Update Main." — updater driver |
| main | `0x22760` (ARM) | update file open: model folder / `OPENINGFILE`, filename by kind |
| main | `0x22510` (ARM) | per-kind processing (1 flash, 2 micom, 3 TW88, 4 BT, 5 none) |
| main | `0x46BA4` | flash writer: classify tag, erase, write, verify |
| main | `0x9251E` | update-file byte-sum check |
| recovery | `0x6678`–`0x6776` | model-folder selection |
| recovery | `0x384C` / `0x38D0` | tag classification / flash write |
| recovery | `0x69DE` | update-file byte-sum check |
