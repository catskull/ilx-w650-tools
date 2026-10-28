# Installing a patched firmware

## Before you start

- Keep a copy of your **original** `Alpine_SYSTEM.FIR`. It is how you go back to stock.
- Verify the patched file: `python ilx_w650.py verify Alpine_SYSTEM.FIR --reference original.FIR`
  (the web tool already checks its own output; this adds the device-code emulation).
- Understand the one unrecoverable case (below) and decide whether you want an SPI programmer
  on hand before flashing. If you have one, reading the chip once beforehand gives you an exact
  backup of your unit.

## Preparing the USB stick

1. Format it **FAT32** with an **MBR** (not GPT) partition table. Any size works.
2. Create a folder **`iLX-W650`** in the root.
3. Put the patched **`Alpine_SYSTEM.FIR`** in that folder (exactly 8,388,608 bytes).
4. Put **nothing else** in that folder. The updater also flashes `micom.srec`, `tw8_mcu.bin`,
   `tw8_img.bin` and `Alpine_BT.dfu` if they are present — do not change other chips at the same time.

```
USB:\
└── iLX-W650\
    └── Alpine_SYSTEM.FIR
```

The unit looks in the folder named after its model; files in the root are ignored.

On macOS (double-check the disk identifier — this erases it):

```sh
diskutil list external                                   # find your stick, e.g. /dev/disk4
diskutil eraseDisk FAT32 USBSTICK MBRFormat /dev/disk4
mkdir /Volumes/USBSTICK/iLX-W650
cp -X Alpine_SYSTEM.FIR /Volumes/USBSTICK/iLX-W650/      # -X: no AppleDouble "._" files
diskutil eject /dev/disk4
```

`python ilx_w650.py wallpaper … -o out --usb` writes `out/iLX-W650/Alpine_SYSTEM.FIR` so you can copy
the `iLX-W650` folder as-is.

## Flashing

1. Power the unit reliably: engine running or a battery charger connected. Do not turn the
   ignition off during the update.
2. Insert the stick and start **Firmware Upgrade** from the unit's settings, following Alpine's
   normal update procedure for the iLX-W650.
3. Wait until it reports completion and restarts.

What happens: the unit reads the file, checks its size and checksum, erases the flash from
`0x180000` to the end (main firmware and footer), writes, reads back to verify, and reboots. The boot
loader, recovery and `pre_main` are not touched by a `_SYS` image.

## Going back to stock

Flash your original `Alpine_SYSTEM.FIR` exactly the same way. There is no version or downgrade check.

## If something goes wrong

| Situation | What you see | Recovery |
|---|---|---|
| Update rejected (wrong size, bad checksum, no folder) | an error on screen, nothing written | fix the stick and retry |
| Power lost or write failed during flashing | unit boots into its recovery updater | recovery looks for `iLX-W650\Alpine_SYSTEM.FIR` on USB/SD — insert the stick (stock or patched) and let it flash |
| Flash completed but main firmware is broken | stuck on the boot logo, or crashes/reboots | **not recoverable over USB**: recovery only runs when the footer is invalid, and the footer is valid. Reprogram the serial flash with an SPI programmer (e.g. CH341A + SOIC-8 clip) using your original image, or erase the footer block (`0x7F0000`–`0x7FFFFF`) so the next boot enters recovery |

The third case is what [VERIFICATION.md](VERIFICATION.md) guards against. Identify the flash chip
on your board before attempting to clip onto it.
