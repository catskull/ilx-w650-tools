"""Run pieces of the head unit's own code under Unicorn (big-endian ARM926).

- boot_unrar: the flash image's stage-2 boot loader unpacker (extracts any archive)
- pre_main_unpack_main: pre_main's unpacker, exactly as used at boot for main_16.bin
- show_bg: main_16.bin's background routine, capturing the framebuffer it fills

Unicorn notes: keep its default CPSR (the E bit selects big-endian data) and SCTLR.
"""
import struct
import zlib

from unicorn import UC_ARCH_ARM, UC_MODE_ARM, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE, Uc, UcError
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
                               UC_ARM_REG_R2, UC_ARM_REG_SP, UC_CPU_ARM_926)

from . import layout as L

SCRATCH = 0x02000000          # stack, size word, return trap
RETURN = SCRATCH              # emulation stops when code returns here
SIZE_WORD = SCRATCH + 0x90000
STACK_TOP = SCRATCH + 0x80000


class EmulationError(Exception):
    pass


def _cpu():
    uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_BIG_ENDIAN)
    uc.ctl_set_cpu_model(UC_CPU_ARM_926)
    uc.mem_map(SCRATCH, 0x00100000)
    return uc


def _call(uc, entry, args, thumb=False):
    uc.reg_write(UC_ARM_REG_SP, STACK_TOP)
    for reg, val in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2), args):
        uc.reg_write(reg, val)
    uc.reg_write(UC_ARM_REG_LR, RETURN | (1 if thumb else 0))
    try:
        uc.emu_start(entry | (1 if thumb else 0), RETURN)
    except UcError as e:
        raise EmulationError(f'{e} at pc={uc.reg_read(UC_ARM_REG_PC):#x}') from e
    return uc.reg_read(UC_ARM_REG_R0)


def _unpacked(uc, ret, what):
    size = struct.unpack('>I', uc.mem_read(SIZE_WORD, 4))[0]
    if ret != 1 or not size:
        raise EmulationError(f'{what}: unpacker reported failure (ret={ret}, size={size})')
    return bytes(uc.mem_read(0, size))


def boot_unrar(image, archive_offset):
    """Unpack the archive at archive_offset with the image's own stage-2 boot loader."""
    uc = _cpu()
    uc.mem_map(0x00000000, 0x01000000)
    uc.mem_map(L.BOOT_STAGE2_RAM, 0x00100000)
    uc.mem_map(L.FLASH_BASE, L.FLASH_SIZE)
    uc.mem_write(L.FLASH_BASE, bytes(image))
    a, b = L.BOOT_STAGE2
    uc.mem_write(L.BOOT_STAGE2_RAM, bytes(image[a:b]))
    ret = _call(uc, L.BOOT_UNRAR, (0, SIZE_WORD, L.FLASH_BASE + archive_offset))
    return _unpacked(uc, ret, f'boot loader unpack of {archive_offset:#x}')


def extract_all(image, archives):
    """Extract every archive with the boot loader and check each against its RAR CRC."""
    out = {}
    for a in archives:
        data = boot_unrar(image, a.offset)
        if len(data) != a.unp_size or (zlib.crc32(data) & 0xFFFFFFFF) != a.file_crc:
            raise EmulationError(f'{a.name}: extracted data fails its CRC')
        out[a.name] = data
    return out


def pre_main_unpack_main(image, pre_main):
    """Unpack main_16.bin from the image exactly as pre_main does at boot."""
    uc = _cpu()
    uc.mem_map(0x00000000, 0x01000000)
    uc.mem_map(L.PRE_MAIN_BASE, 0x01000000)
    uc.mem_map(L.FLASH_BASE, L.FLASH_SIZE)
    uc.mem_write(L.PRE_MAIN_BASE, bytes(pre_main))
    uc.mem_write(L.FLASH_BASE, bytes(image))
    ret = _call(uc, L.PRE_MAIN_UNRAR, (0, SIZE_WORD, L.FLASH_BASE + L.MAIN_ARCHIVE), thumb=True)
    return _unpacked(uc, ret, 'pre_main unpack of main_16.bin')


def show_bg(main, bg_type):
    """Run Show_BG_Image(bg_type) and return the framebuffer it fills.

    Types: 3 = normal wallpaper, 8 = dimmed wallpaper, 9 = generated black.
    Logging, a lock helper and the framebuffer getter are stubbed; emulation stops
    when the routine hands the buffer to the display hardware.
    """
    fb_addr = 0x03000000
    done = 0x7AEEC
    stubs = {0x0CDA6: None, 0x7AB1E: None, 0x06546: fb_addr}
    uc = _cpu()
    uc.mem_map(0x00000000, 0x02000000)
    uc.mem_map(fb_addr, 0x00100000)
    uc.mem_write(0, bytes(main))

    def on_code(uc, addr, size, _):
        if addr in stubs:
            if stubs[addr] is not None:
                uc.reg_write(UC_ARM_REG_R0, stubs[addr])
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        elif addr == done:
            uc.emu_stop()

    for a in list(stubs) + [done]:
        uc.hook_add(UC_HOOK_CODE, on_code, begin=a, end=a)
    _call(uc, L.SHOW_BG_IMAGE, (bg_type,), thumb=True)
    if uc.reg_read(UC_ARM_REG_PC) != done:
        raise EmulationError(f'Show_BG_Image({bg_type}) did not reach the display hand-off')
    return bytes(uc.mem_read(fb_addr, L.COPY_BLOCK))
