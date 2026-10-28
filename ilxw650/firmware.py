"""Parse, validate and rebuild Alpine_SYSTEM.FIR flash images."""
import struct
import zlib
from dataclasses import dataclass

from . import layout as L
from . import rar20


class FirmwareError(Exception):
    """The input is not a supported, intact iLX-W650 flash image."""


@dataclass
class Archive:
    name: str
    offset: int           # where 'Rar!' starts
    length: int           # value of the 4-byte prefix
    header_offset: int
    header_size: int
    pack_size: int
    unp_size: int
    file_crc: int
    unp_ver: int
    method: int
    header_crc_ok: bool

    @property
    def data_offset(self):
        return self.header_offset + self.header_size


def footer_sum(image):
    return sum(image[:L.FOOTER_SUM]) & 0xFFFFFFFF


def stored_footer_sum(image):
    return struct.unpack_from('>I', image, L.FOOTER_SUM)[0]


def parse_archive(image, offset):
    """Parse the single-file RAR 1.5-4.x archive at offset."""
    if image[offset:offset + 7] != b'Rar!\x1a\x07\x00':
        raise FirmwareError(f'no RAR archive at {offset:#x}')
    main_size = struct.unpack_from('<H', image, offset + 7 + 5)[0]
    h = offset + 7 + main_size
    if image[h + 2] != 0x74:
        raise FirmwareError(f'archive at {offset:#x} does not start with a file header')
    hsize = struct.unpack_from('<H', image, h + 5)[0]
    pack, unp = struct.unpack_from('<II', image, h + 7)
    fcrc = struct.unpack_from('<I', image, h + 16)[0]
    name_len = struct.unpack_from('<H', image, h + 26)[0]
    hcrc = struct.unpack_from('<H', image, h)[0]
    return Archive(
        name=image[h + 32:h + 32 + name_len].decode('latin1'),
        offset=offset,
        length=struct.unpack_from('>I', image, offset - 4)[0],
        header_offset=h, header_size=hsize, pack_size=pack, unp_size=unp, file_crc=fcrc,
        unp_ver=image[h + 24], method=image[h + 25],
        header_crc_ok=(zlib.crc32(image[h + 2:h + hsize]) & 0xFFFF) == hcrc,
    )


def check_image(image):
    """Structural checks on a flash image. Returns a list of (check, ok, detail)."""
    results = [('size is 8 MiB', len(image) == L.FLASH_SIZE, f'{len(image)} bytes')]
    if len(image) != L.FLASH_SIZE:
        return results
    tag = bytes(image[L.FOOTER_TAG:L.FOOTER_TAG + 14])
    results.append(('footer tag', tag == L.SYS_TAG, tag.decode('latin1', 'replace')))
    results.append(('footer marker', image[L.FOOTER_MARKER:L.FOOTER_MARKER + 8] == b'\x5a' * 4 + b'\xa5' * 4, ''))
    s, stored = footer_sum(image), stored_footer_sum(image)
    results.append(('footer checksum', s == stored, f'computed {s:#010x}, stored {stored:#010x}'))
    results.append(('boot marker "Rar" at 0x180000', image[L.MAIN_ARCHIVE:L.MAIN_ARCHIVE + 3] == b'Rar', ''))
    for name, off in L.ARCHIVES.items():
        try:
            a = parse_archive(image, off)
            ok = (a.name == name and a.header_crc_ok and a.unp_ver == 20 and a.method == 0x33
                  and a.data_offset + a.pack_size - off == a.length)
            results.append((f'archive {name}', ok,
                            f'{a.pack_size} -> {a.unp_size} bytes, CRC {a.file_crc:#010x}'
                            + ('' if ok else f' (name={a.name}, header crc ok={a.header_crc_ok})')))
        except FirmwareError as e:
            results.append((f'archive {name}', False, str(e)))
    return results


def require_valid(image):
    failed = [(c, d) for c, ok, d in check_image(image) if not ok]
    if failed:
        raise FirmwareError('; '.join(f'{c}: {d}' if d else c for c, d in failed))


def masked_crc(main):
    """CRC32 of main_16.bin with the wallpaper planes zeroed: identifies the build."""
    m = bytearray(main)
    for fields in L.COPIES.values():
        for s in fields:
            m[s:s + L.Y_SIZE] = bytes(L.Y_SIZE)
            m[s + L.Y_SIZE + L.PAD:s + L.Y_SIZE + L.PAD + L.UV_SIZE] = bytes(L.UV_SIZE)
    return zlib.crc32(m) & 0xFFFFFFFF


def require_supported_main(main):
    if len(main) != L.MAIN_SIZE:
        raise FirmwareError(f'main_16.bin is {len(main)} bytes; only the {L.MAIN_SIZE}-byte build is supported')
    fp = masked_crc(main)
    if fp != L.MAIN_MASKED_CRC:
        raise FirmwareError(f'unrecognised main_16.bin build (fingerprint {fp:#010x}); wallpaper offsets may differ')


def rebuild(image, main):
    """Return a new flash image whose main archive holds `main`.

    Everything outside the main slot is kept byte-identical; the length prefix and
    footer checksum are recomputed.
    """
    arch = parse_archive(image, L.MAIN_ARCHIVE)
    template = bytes(image[L.MAIN_ARCHIVE:L.MAIN_ARCHIVE + arch.length])
    archive = rar20.build_archive(template, L.MAIN_NAME, main, rar20.compress(main))
    if L.MAIN_ARCHIVE + len(archive) > L.SLOT_END:
        raise FirmwareError(f'compressed main firmware too large ({len(archive)} bytes)')
    out = bytearray(image)
    out[L.MAIN_ARCHIVE:L.SLOT_END] = b'\xff' * (L.SLOT_END - L.MAIN_ARCHIVE)
    struct.pack_into('>I', out, L.MAIN_ARCHIVE - 4, len(archive))
    out[L.MAIN_ARCHIVE:L.MAIN_ARCHIVE + len(archive)] = archive
    struct.pack_into('>I', out, L.FOOTER_SUM, footer_sum(out))
    return bytes(out)


def unchanged_outside_main(original, rebuilt):
    """True if only the main slot and the footer checksum differ."""
    return (original[:L.MAIN_ARCHIVE - 4] == rebuilt[:L.MAIN_ARCHIVE - 4]
            and original[L.SLOT_END:L.FOOTER_SUM] == rebuilt[L.SLOT_END:L.FOOTER_SUM]
            and original[L.FOOTER_SUM + 4:] == rebuilt[L.FOOTER_SUM + 4:])
