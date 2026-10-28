#!/usr/bin/env python3
"""EXPERIMENTAL: boot main_16.bin from its reset vector under Unicorn.

Usage: boot_main.py <main_16.bin> [seconds]

This is the starting point for a full-system emulator, not a working one. It gets
through the C runtime start-up and stops at the first hardware dependency (the clock
controller at 0x3C500000: reading zeros makes the firmware divide by zero and exit).
All peripheral space is mapped as logged MMIO that reads as zero. See docs/FIRMWARE.md
("Emulation notes") for what a fuller emulator would need.

Unicorn specifics handled here:
  - keep the CPSR E bit (0x200) when choosing the start mode, or data turns little-endian;
  - ignore the firmware's SCTLR writes (mcr p15,0,Rd,c1,c0,0): Unicorn's mrc returns 0,
    so writing it back would drop big-endian mode;
  - the C library uses ARM semihosting (SVC 0xAB Thumb / 0x123456 ARM).
"""
import collections
import struct
import sys
import time

from unicorn import UC_ARCH_ARM, UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_INTR, UC_MODE_ARM, UC_MODE_BIG_ENDIAN, Uc, UcError
from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_CPU_ARM_926)

main = open(sys.argv[1], 'rb').read()
limit_s = float(sys.argv[2]) if len(sys.argv) > 2 else 20

uc = Uc(UC_ARCH_ARM, UC_MODE_ARM | UC_MODE_BIG_ENDIAN)
uc.ctl_set_cpu_model(UC_CPU_ARM_926)
uc.mem_map(0x00000000, 0x04000000)           # SDRAM; main is linked at 0
uc.mem_write(0, main)

mmio, first_pc = collections.Counter(), {}


def mmio_read(uc, off, size, base):
    key = ((base + off) & ~0xFF, 'R')
    mmio[key] += 1
    first_pc.setdefault(key, uc.reg_read(UC_ARM_REG_PC))
    return 0


def mmio_write(uc, off, size, value, base):
    key = ((base + off) & ~0xFF, 'W')
    mmio[key] += 1
    first_pc.setdefault(key, uc.reg_read(UC_ARM_REG_PC))


for base in (0x30000000, 0x38000000, 0x3C000000):
    uc.mmio_map(base, 0x04000000, mmio_read, base, mmio_write, base)

# Ignore SCTLR writes (see module docstring).
for site in (o for o in range(0, len(main) - 3, 4)
             if struct.unpack('>I', main[o:o + 4])[0] & 0xFFFF0FFF == 0xEE010F10):
    uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, _: uc.reg_write(UC_ARM_REG_PC, a + 4), begin=site, end=site)

console, t0 = [], time.time()


def semihost(uc, intno, _):
    pc = uc.reg_read(UC_ARM_REG_PC)
    op, arg = uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)
    rd32 = lambda a: struct.unpack('>I', uc.mem_read(a, 4))[0]
    ret = 0
    if op == 0x03:                                   # WRITEC
        console.append(chr(uc.mem_read(arg, 1)[0]))
    elif op == 0x04:                                 # WRITE0
        s = bytearray()
        while (c := uc.mem_read(arg + len(s), 1)[0]):
            s.append(c)
        console.append(s.decode('latin1'))
    elif op == 0x05:                                 # WRITE
        console.append(bytes(uc.mem_read(rd32(arg + 4), rd32(arg + 8))).decode('latin1'))
    elif op == 0x01:                                 # OPEN -> fake handle
        ret = 1
    elif op == 0x15:                                 # GET_CMDLINE -> empty
        uc.mem_write(rd32(arg), b'\0')
        uc.mem_write(arg + 4, b'\0\0\0\0')
    elif op == 0x16:                                 # HEAPINFO
        uc.mem_write(rd32(arg), struct.pack('>IIII', 0x01000000, 0x02F00000, 0x03F00000, 0x03E00000))
    elif op == 0x18:                                 # EXIT
        print(f'SYS_EXIT {arg:#x} at {pc:#x}')
        uc.emu_stop()
    uc.reg_write(UC_ARM_REG_R0, ret)


uc.hook_add(UC_HOOK_INTR, semihost)
blocks = collections.Counter()
uc.hook_add(UC_HOOK_BLOCK, lambda uc, a, s, _: blocks.update([a]))
uc.reg_write(UC_ARM_REG_CPSR, 0xD3 | (uc.reg_read(UC_ARM_REG_CPSR) & 0x200))   # SVC, IRQ/FIQ off, keep E

try:
    uc.emu_start(0, 0xFFFFFFF0, timeout=int(limit_s * 1e6))
    why = 'stopped/timeout'
except UcError as e:
    why = str(e)
print(f'ran {time.time() - t0:.1f}s, pc={uc.reg_read(UC_ARM_REG_PC):#x} ({why}), {len(blocks)} basic blocks')
print('console:', repr(''.join(console)))
print('hot blocks:', [(hex(a), n) for a, n in blocks.most_common(5)])
for (page, rw), cnt in sorted(mmio.items()):
    print(f'  MMIO {rw} {page:#010x} x{cnt} (first from {first_pc[(page, rw)]:#x})')
