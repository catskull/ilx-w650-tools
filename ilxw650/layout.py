"""Fixed facts about the iLX-W650 flash image and main firmware build this project supports.

See docs/FIRMWARE.md and docs/WALLPAPER.md for how each value was established.
"""

# ---------------------------------------------------------------- flash image
FLASH_SIZE = 0x800000            # Alpine_SYSTEM.FIR is a raw image of the 8 MiB serial flash
FLASH_BASE = 0x20000000          # where the SoC maps the serial flash

BOOT_STAGE2 = (0x000C00, 0x001FD8)   # stage-2 boot loader, copied to RAM at 0x01F00000
BOOT_STAGE2_RAM = 0x01F00000
BOOT_UNRAR = 0x01F0106C              # boot loader's RAR unpacker: (dest, &size, src)

ARCHIVES = {                     # offset of each RAR archive (a 4-byte BE length precedes it)
    'recovery_16.bin': 0x002000,
    'pre_main_16.bin': 0x040000,
    'main_16.bin': 0x180000,
}
MAIN_ARCHIVE = ARCHIVES['main_16.bin']
SLOT_END = 0x7F0000              # first byte of the footer erase block
FOOTER_TAG = 0x7FFFC0            # "ALPINE_ILX_SYS", version, ...
FOOTER_MARKER = 0x7FFFE0         # 5A5A5A5A A5A5A5A5
FOOTER_SUM = 0x7FFFF0            # 32-bit sum of bytes 0x000000..0x7FFFEF
TAG_PREFIX = b'ALPINE_ILX'
SYS_TAG = b'ALPINE_ILX_SYS'

# ------------------------------------------------------------ main_16.bin build
MAIN_NAME = 'main_16.bin'
MAIN_SIZE = 9499836
MAIN_MASKED_CRC = 0x2B5C8407     # CRC32 with the wallpaper planes zeroed (stock or re-patched)

PRE_MAIN_BASE = 0x01000000       # pre_main_16.bin load address
PRE_MAIN_UNRAR = 0x01004C60      # pre_main's unpacker for main (Thumb): (dest, &size, src)
SHOW_BG_IMAGE = 0x7ACEA          # main: draws the home-screen background (Thumb)

# ------------------------------------------------------------------ wallpaper
WIDTH, HEIGHT = 800, 480
Y_SIZE = WIDTH * (HEIGHT // 2)   # one 800x240 field
PAD = 512                        # zero bytes between a field's Y and UV planes
UV_SIZE = Y_SIZE // 2            # interleaved U/V, 2x2 subsampled
COPIES = {                       # field start offsets: (even lines, odd lines)
    'normal': (0x637CB8, 0x67E4B8),
    'dimmed': (0x6C50B8, 0x70B8B8),
}
COPY_BLOCK = 0x8D400             # bytes Show_BG_Image copies for one copy (both fields + gaps)
DIM_FACTOR = 0.30                # dimmed copy = RGB x 0.30 (measured from stock)
