#!/usr/bin/env python3
"""Disassemble big-endian ARM or Thumb code with literal/string annotations.

Usage: disasm.py <file> <load base> <start> <end> [arm|thumb]     (addresses in hex)

Examples
  main_16.bin (loads at 0):           disasm.py main_16.bin 0 7acea 7ad90 thumb
  pre_main_16.bin (0x01000000):       disasm.py pre_main_16.bin 1000000 1002ea0 1002f54 thumb
  flash stage 2 (file 0xC00 -> 0x01F00000):
                                      disasm.py Alpine_SYSTEM.FIR 1eff400 1f002bc 1f00328 arm

PC-relative loads show the loaded value; values/ADR targets that point at ASCII or
UTF-16BE text inside the file show the text.
"""
import struct
import sys

from capstone import CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_BIG_ENDIAN, CS_MODE_THUMB, Cs

path, base, start, end = sys.argv[1], *(int(a, 16) for a in sys.argv[2:5])
thumb = len(sys.argv) > 5 and sys.argv[5] == 'thumb'
data = open(path, 'rb').read()

md = Cs(CS_ARCH_ARM, CS_MODE_BIG_ENDIAN | (CS_MODE_THUMB if thumb else CS_MODE_ARM))
md.detail = True
md.skipdata = True


def text_at(addr):
    o = addr - base
    if not 0 <= o < len(data) - 2:
        return ''
    if 32 <= data[o] < 127:
        s = data[o:o + 80].split(b'\0')[0]
        if len(s) >= 3:
            return ' "' + s.decode('latin1').replace('\n', '\\n').replace('\r', '\\r') + '"'
    if data[o] == 0 and 32 <= data[o + 1] < 127:
        s = data[o:o + 120].decode('utf-16-be', 'replace').split('\0')[0]
        if len(s) >= 3:
            return f' u"{s}"'
    return ''


for i in md.disasm(data[start - base:end - base], start):
    if i.mnemonic == '.byte':
        print(f'{i.address:08x}: .byte   {i.op_str}')
        continue
    note = ''
    pc = (i.address + (4 if thumb else 8))
    is_pc_rel = i.mnemonic == 'adr' or (i.mnemonic in ('add', 'sub') and 'pc' in i.op_str)
    if is_pc_rel and i.operands and i.operands[-1].type == 2:
        imm = i.operands[-1].imm
        t = (pc & ~3) + imm if i.mnemonic in ('adr', 'add') else pc - imm
        note = f'  ; {t:#x}' + text_at(t)
    elif i.mnemonic.startswith('ldr') and '[pc' in i.op_str:
        lit = (pc & ~3) + i.operands[1].mem.disp
        if 0 <= lit - base < len(data) - 3:
            v = struct.unpack('>I', data[lit - base:lit - base + 4])[0]
            note = f'  ; ={v:#010x}' + text_at(v)
    print(f'{i.address:08x}: {i.mnemonic:8} {i.op_str}{note}')
