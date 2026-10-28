#!/usr/bin/env python3
"""Find code that references an address in a big-endian ARM/Thumb image.

Usage: xref.py <file> <load base> <target> [<target> ...]      (hex)

For each target it reports:
  - 32-bit literal-pool words equal to the target, and the Thumb `ldr rX, [pc, #n]`
    instructions within 1 KB that load them;
  - Thumb `adr` instructions that compute the target;
  - ARM `add/sub rX, pc, #imm` and `ldr rX, [pc, #±imm]` that reach it;
  - Thumb BL, ARM BL and ARM BLX calls to it (for function entry points).

main_16.bin mixes Thumb (most code) and ARM (e.g. the USB updater at 0x21000-0x23A00),
so check both kinds before concluding something is unused.
"""
import struct
import sys

path, base = sys.argv[1], int(sys.argv[2], 16)
targets = [int(t, 16) for t in sys.argv[3:]]
d = open(path, 'rb').read()
n = len(d)


def rot(imm12):
    r, v = (imm12 >> 8) * 2, imm12 & 0xFF
    return ((v >> r) | (v << (32 - r))) & 0xFFFFFFFF if r else v


for t in targets:
    print(f'== {t:#x}')
    t_off = t - base
    for o in range(0, n - 3, 4):
        if struct.unpack('>I', d[o:o + 4])[0] in (t, t | 1):
            users = []
            for u in range(max(0, o - 1024), o, 2):
                h = struct.unpack('>H', d[u:u + 2])[0]
                if h >> 11 == 0b01001 and ((base + u + 4) & ~3) + (h & 0xFF) * 4 == base + o:
                    users.append(f'{base + u:#x}')
            print(f'  literal at {base + o:#x}' + (f' loaded by thumb ldr at {users}' if users else ' (data pointer or ARM load)'))
    for u in range(max(0, t_off - 1024), min(n - 1, max(0, t_off)), 2):
        h = struct.unpack('>H', d[u:u + 2])[0]
        if h >> 11 == 0b10100 and ((base + u + 4) & ~3) + (h & 0xFF) * 4 == t:
            print(f'  thumb adr at {base + u:#x}')
    for o in range(0, n - 3, 4):
        w = struct.unpack('>I', d[o:o + 4])[0]
        cond = w >> 28
        if (w & 0x0FFF0000) in (0x028F0000, 0x024F0000) and cond != 0xF:
            imm = rot(w & 0xFFF)
            if (base + o + 8 + imm if (w & 0x0FFF0000) == 0x028F0000 else base + o + 8 - imm) == t:
                print(f'  arm add/sub pc at {base + o:#x}')
        elif (w & 0x0F7F0000) == 0x051F0000 and cond != 0xF:
            lit = o + 8 + ((w & 0xFFF) if w & (1 << 23) else -(w & 0xFFF))
            if 0 <= lit < n - 3 and struct.unpack('>I', d[lit:lit + 4])[0] in (t, t | 1):
                print(f'  arm ldr literal at {base + o:#x}')
        elif (w & 0xFE000000) == 0xFA000000 or ((w & 0x0F000000) == 0x0B000000 and cond != 0xF):
            off = w & 0xFFFFFF
            off -= (1 << 24) if off & (1 << 23) else 0
            dest = base + o + 8 + off * 4 + (((w >> 23) & 2) if (w & 0xFE000000) == 0xFA000000 else 0)
            if dest == t:
                print(f'  arm {"blx" if cond == 0xF else "bl"} at {base + o:#x}')
    for o in range(0, n - 4, 2):
        h1, h2 = struct.unpack('>HH', d[o:o + 4])
        if h1 >> 11 == 0b11110 and (h2 >> 11) in (0b11111, 0b11101):
            off = ((h1 & 0x7FF) << 12) | ((h2 & 0x7FF) << 1)
            off -= (1 << 23) if off & (1 << 22) else 0
            dest = base + o + 4 + off
            if h2 >> 11 == 0b11101:
                dest &= ~3
            if dest == t:
                print(f'  thumb {"bl" if h2 >> 11 == 0b11111 else "blx"} at {base + o:#x}')
