"""End-to-end tests. They need a stock image, which is not distributed with this project:

    ILX_W650_FIR=/path/to/Alpine_SYSTEM.FIR python -m pytest
"""
import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ilxw650 import emu, firmware, layout as L, wallpaper  # noqa: E402
import ilx_w650  # noqa: E402

FIR = os.environ.get('ILX_W650_FIR')
pytestmark = pytest.mark.skipif(not FIR, reason='set ILX_W650_FIR to a stock Alpine_SYSTEM.FIR')


@pytest.fixture(scope='module')
def image():
    return pathlib.Path(FIR).read_bytes()


@pytest.fixture(scope='module')
def binaries(image):
    return emu.extract_all(image, [firmware.parse_archive(image, o) for o in L.ARCHIVES.values()])


def test_stock_image_passes_structural_checks(image):
    assert all(ok for _, ok, _ in firmware.check_image(image))


def test_boot_loader_extracts_every_archive_with_valid_crc(binaries):
    assert {n: len(d) for n, d in binaries.items()} == {
        'recovery_16.bin': 395408, 'pre_main_16.bin': 796064, 'main_16.bin': L.MAIN_SIZE}


def test_main_build_is_supported(binaries):
    firmware.require_supported_main(binaries[L.MAIN_NAME])


def test_rebuild_unmodified_main_round_trips_through_device_unpacker(image, binaries):
    main = binaries[L.MAIN_NAME]
    rebuilt = firmware.rebuild(image, main)
    assert firmware.unchanged_outside_main(image, rebuilt)
    assert all(ok for _, ok, _ in firmware.check_image(rebuilt))
    assert emu.pre_main_unpack_main(rebuilt, binaries['pre_main_16.bin']) == main


def test_device_unpacker_does_not_detect_corruption(image, binaries):
    # Documents why verification matters: pre_main never checks the CRC.
    bad = bytearray(image)
    bad[L.MAIN_ARCHIVE + 2_000_000] ^= 0x5A
    out = emu.pre_main_unpack_main(bytes(bad), binaries['pre_main_16.bin'])
    assert len(out) == L.MAIN_SIZE and out != binaries[L.MAIN_NAME]


def test_black_wallpaper_matches_firmware_generated_black(binaries):
    rgb = wallpaper.load_image(None, background=(0, 0, 0))
    patched = wallpaper.patch(binaries[L.MAIN_NAME], rgb)
    black = emu.show_bg(patched, 9)
    assert emu.show_bg(patched, 3) == black
    assert emu.show_bg(patched, 8) == black


def test_stock_wallpaper_round_trips_through_converter(binaries):
    main = binaries[L.MAIN_NAME]
    rgb = np.asarray(wallpaper.render(main, 'normal'), np.float32)
    patched = wallpaper.patch(main, rgb)
    a = np.frombuffer(main, np.uint8).astype(int)
    b = np.frombuffer(patched, np.uint8).astype(int)
    s = L.COPIES['normal'][0]
    assert np.abs(a[s:s + L.Y_SIZE] - b[s:s + L.Y_SIZE]).mean() < 1.0


def test_wallpaper_command_end_to_end(image, binaries, tmp_path):
    src = tmp_path / 'gradient.png'
    x = np.linspace(0, 255, 1024, dtype=np.uint8)
    from PIL import Image
    Image.fromarray(np.dstack([np.tile(x, (600, 1))] * 3)).save(src)
    out = tmp_path / 'usb'
    args = ['wallpaper', FIR, str(src), '--fit', 'contain', '-o', str(out), '--usb']
    r = subprocess.run([sys.executable, str(ROOT / 'ilx_w650.py'), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    built = (out / 'iLX-W650' / 'Alpine_SYSTEM.FIR').read_bytes()
    results, _ = ilx_w650.verify_patched(image, built, emu.boot_unrar(built, L.MAIN_ARCHIVE))
    assert all(ok for _, ok, _ in results)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_web_core_output_passes_device_emulation(image, binaries, tmp_path):
    r = subprocess.run(['node', str(ROOT / 'web' / 'test' / 'test_core.js'), FIR, str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    js = (tmp_path / 'js_black.FIR').read_bytes()
    expected = wallpaper.patch(binaries[L.MAIN_NAME], wallpaper.load_image(None))
    results, _ = ilx_w650.verify_patched(image, js, expected)
    assert all(ok for _, ok, _ in results), results
