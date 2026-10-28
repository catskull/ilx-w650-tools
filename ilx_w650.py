#!/usr/bin/env python3
"""Alpine iLX-W650 firmware tools.

  info       check an Alpine_SYSTEM.FIR and show its layout
  extract    extract recovery/pre_main/main (and the current wallpaper) using the image's own boot loader
  wallpaper  build a verified Alpine_SYSTEM.FIR with a new home-screen wallpaper
  verify     re-run the safety checks on a (patched) Alpine_SYSTEM.FIR

Run `python ilx_w650.py <command> -h` for options. See README.md before flashing anything.
"""
import argparse
import pathlib
import sys
import time
import zlib

from ilxw650 import emu, firmware, layout as L, wallpaper


def read_image(path):
    data = pathlib.Path(path).read_bytes()
    return data


def archives(image):
    return [firmware.parse_archive(image, off) for off in L.ARCHIVES.values()]


def print_checks(results):
    ok_all = True
    for check, ok, detail in results:
        ok_all &= ok
        print(f'  [{"ok" if ok else "FAIL"}] {check}' + (f' — {detail}' if detail else ''))
    return ok_all


# ------------------------------------------------------------------- commands

def cmd_info(args):
    image = read_image(args.fir)
    print(f'{args.fir}')
    ok = print_checks(firmware.check_image(image))
    if not ok:
        return 1
    tag = image[L.FOOTER_TAG:L.FOOTER_TAG + 16].rstrip(b'\xff').decode('latin1')
    ver = image[L.FOOTER_TAG + 16:L.FOOTER_TAG + 19].decode('latin1', 'replace')
    print(f'  footer: {tag}  version field "{ver}"')
    print('  flash map:')
    print(f'    0x000000  boot loader (stage 2 at {L.BOOT_STAGE2[0]:#x}-{L.BOOT_STAGE2[1]:#x})')
    for a in archives(image):
        print(f'    {a.offset:#08x}  {a.name:<16} {a.pack_size:>8} -> {a.unp_size:>8} bytes')
    print(f'    {L.FOOTER_TAG:#08x}  footer, checksum at {L.FOOTER_SUM:#x}')
    if args.deep:
        data = emu.extract_all(image, [firmware.parse_archive(image, L.MAIN_ARCHIVE)])
        try:
            firmware.require_supported_main(data[L.MAIN_NAME])
            print('  main_16.bin build: supported (wallpaper patching available)')
        except firmware.FirmwareError as e:
            print(f'  main_16.bin build: {e}')
    return 0


def cmd_extract(args):
    image = read_image(args.fir)
    firmware.require_valid(image)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t = time.time()
    files = emu.extract_all(image, archives(image))
    for name, data in files.items():
        (out / name).write_bytes(data)
        print(f'  {name:<16} {len(data):>8} bytes  (CRC ok)')
    print(f'extracted with the image\'s own boot loader in {time.time() - t:.1f}s -> {out}')
    main = files[L.MAIN_NAME]
    try:
        firmware.require_supported_main(main)
        for which in L.COPIES:
            p = out / f'wallpaper_{which}.png'
            wallpaper.render(main, which).save(p)
            print(f'  {p.name}')
    except firmware.FirmwareError as e:
        print(f'  (wallpaper not exported: {e})')
    return 0


def verify_patched(original, patched, expected_main):
    """Run every check on a rebuilt image. Returns a list of (check, ok, detail)."""
    results = firmware.check_image(patched)
    results.append(('only main slot + checksum changed', firmware.unchanged_outside_main(original, patched), ''))
    pre_main = emu.boot_unrar(patched, L.ARCHIVES['pre_main_16.bin'])
    t = time.time()
    unpacked = emu.pre_main_unpack_main(patched, pre_main)
    results.append(('device unpacker (pre_main, emulated) output == patched main_16.bin',
                    unpacked == expected_main, f'{time.time() - t:.1f}s'))
    for bg_type, which in ((3, 'normal'), (8, 'dimmed')):
        fb = emu.show_bg(unpacked, bg_type)
        start = L.COPIES[which][0]
        results.append((f'Show_BG_Image({bg_type}) draws the {which} copy',
                        fb == unpacked[start:start + L.COPY_BLOCK], ''))
    return results, unpacked


def cmd_wallpaper(args):
    if (args.image is None) == (args.color is None):
        sys.exit('give either an IMAGE or --color #RRGGBB')
    image = read_image(args.fir)
    firmware.require_valid(image)
    print('extracting main_16.bin with the boot loader…')
    main = emu.boot_unrar(image, L.MAIN_ARCHIVE)
    firmware.require_supported_main(main)

    bg = wallpaper.parse_color(args.color or args.background)
    rgb = wallpaper.load_image(args.image, args.fit, bg)
    patched_main = wallpaper.patch(main, rgb)
    print('compressing and rebuilding the flash image…')
    t = time.time()
    patched = firmware.rebuild(image, patched_main)
    arch = firmware.parse_archive(patched, L.MAIN_ARCHIVE)
    print(f'  main archive {arch.length} bytes (stock {firmware.parse_archive(image, L.MAIN_ARCHIVE).length}), {time.time() - t:.1f}s')

    print('verifying…')
    results, unpacked = verify_patched(image, patched, patched_main)
    if not print_checks(results):
        sys.exit('verification failed — nothing written')

    out = pathlib.Path(args.out)
    if args.usb:
        out = out / 'iLX-W650' / 'Alpine_SYSTEM.FIR'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(patched)
    for which in L.COPIES:
        wallpaper.render(unpacked, which).save(out.with_name(f'preview_{which}.png'))
    print(f'wrote {out} ({len(patched)} bytes) and preview_*.png next to it')
    print('copy the iLX-W650 folder to the root of a FAT32 USB stick; see docs/UPDATING.md')
    return 0


def cmd_verify(args):
    image = read_image(args.fir)
    ok = print_checks(firmware.check_image(image))
    if not ok:
        return 1
    pre_main = emu.boot_unrar(image, L.ARCHIVES['pre_main_16.bin'])
    main_boot = emu.boot_unrar(image, L.MAIN_ARCHIVE)
    main_dev = emu.pre_main_unpack_main(image, pre_main)
    a = firmware.parse_archive(image, L.MAIN_ARCHIVE)
    checks = [('pre_main unpacker output matches boot-loader unpack and RAR CRC',
               main_dev == main_boot and (zlib.crc32(main_dev) & 0xFFFFFFFF) == a.file_crc, '')]
    try:
        firmware.require_supported_main(main_dev)
        checks.append(('supported main_16.bin build', True, ''))
        for bg_type, which in ((3, 'normal'), (8, 'dimmed')):
            fb = emu.show_bg(main_dev, bg_type)
            s = L.COPIES[which][0]
            checks.append((f'Show_BG_Image({bg_type}) draws the {which} copy', fb == main_dev[s:s + L.COPY_BLOCK], ''))
    except firmware.FirmwareError as e:
        checks.append(('supported main_16.bin build', False, str(e)))
    if args.reference:
        ref = read_image(args.reference)
        checks.append(('only main slot + checksum differ from reference', firmware.unchanged_outside_main(ref, image), ''))
    return 0 if print_checks(checks) else 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('info', help='check an image and show its layout')
    s.add_argument('fir')
    s.add_argument('--deep', action='store_true', help='also extract main and check the build is supported')
    s.set_defaults(func=cmd_info)

    s = sub.add_parser('extract', help='extract the three binaries and the current wallpaper')
    s.add_argument('fir')
    s.add_argument('-o', '--out', default='extracted')
    s.set_defaults(func=cmd_extract)

    s = sub.add_parser('wallpaper', help='build an image with a new wallpaper')
    s.add_argument('fir', help='stock (or previously patched) Alpine_SYSTEM.FIR')
    s.add_argument('image', nargs='?', help='any image Pillow can read')
    s.add_argument('--color', help='solid colour instead of an image, e.g. #000000')
    s.add_argument('--fit', choices=wallpaper.FITS, default='cover',
                   help='cover = fill and crop (default), contain = fit inside, stretch')
    s.add_argument('--background', default='#000000', help='padding/transparency colour (default #000000)')
    s.add_argument('-o', '--out', default='Alpine_SYSTEM.FIR', help='output file, or a folder with --usb')
    s.add_argument('--usb', action='store_true', help='write <out>/iLX-W650/Alpine_SYSTEM.FIR (USB layout)')
    s.set_defaults(func=cmd_wallpaper)

    s = sub.add_parser('verify', help='run the safety checks on an image')
    s.add_argument('fir')
    s.add_argument('--reference', help='stock image to compare against')
    s.set_defaults(func=cmd_verify)

    args = p.parse_args()
    try:
        sys.exit(args.func(args))
    except (firmware.FirmwareError, emu.EmulationError, ValueError, OSError) as e:
        sys.exit(f'error: {e}')


if __name__ == '__main__':
    main()
