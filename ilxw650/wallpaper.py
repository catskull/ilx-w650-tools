"""Home-screen wallpaper: image <-> the NV12 fields stored in main_16.bin.

Each copy (normal, dimmed) is an interlaced 800x480 frame stored as two 800x240
fields (even lines, odd lines). A field is a Y plane, 512 zero bytes, then an
interleaved UV plane subsampled 2x2. Colour is full-range BT.601.
"""
import numpy as np
from PIL import Image

from . import layout as L

FITS = ('cover', 'contain', 'stretch')


def parse_color(text):
    t = text.lstrip('#')
    if len(t) != 6:
        raise ValueError(f'colour must look like #RRGGBB, got {text!r}')
    return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))


def load_image(path=None, fit='cover', background=(0, 0, 0)):
    """Return an 800x480 RGB float array from an image file (or a solid colour)."""
    canvas = Image.new('RGB', (L.WIDTH, L.HEIGHT), background)
    if path is not None:
        img = Image.open(path)
        img = img.convert('RGBA')
        if fit == 'stretch':
            img = img.resize((L.WIDTH, L.HEIGHT), Image.LANCZOS)
            canvas.paste(img, (0, 0), img)
        else:
            pick = max if fit == 'cover' else min
            s = pick(L.WIDTH / img.width, L.HEIGHT / img.height)
            img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
            canvas.paste(img, ((L.WIDTH - img.width) // 2, (L.HEIGHT - img.height) // 2), img)
    return np.asarray(canvas, np.float32)


def _field_nv12(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    u = -0.168736 * r - 0.331264 * g + 0.5 * b + 128
    v = 0.5 * r - 0.418688 * g - 0.081312 * b + 128
    h, w = y.shape
    sub = lambda c: c.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    uv = np.stack([sub(u), sub(v)], axis=-1)
    q = lambda a: np.clip(np.round(a), 0, 255).astype(np.uint8).tobytes()
    return q(y), q(uv)


def patch(main, rgb):
    """Write both wallpaper copies (normal + dimmed) into main_16.bin."""
    out = bytearray(main)
    for name, (even, odd) in L.COPIES.items():
        frame = rgb * (L.DIM_FACTOR if name == 'dimmed' else 1.0)
        for start, field in ((even, frame[0::2]), (odd, frame[1::2])):
            if any(out[start + L.Y_SIZE:start + L.Y_SIZE + L.PAD]):
                raise ValueError(f'unexpected data in the pad after {start:#x}')
            y, uv = _field_nv12(field)
            out[start:start + L.Y_SIZE] = y
            out[start + L.Y_SIZE + L.PAD:start + L.Y_SIZE + L.PAD + L.UV_SIZE] = uv
    return bytes(out)


def _field_rgb(buf, y0, c0):
    y = np.frombuffer(buf, np.uint8, L.Y_SIZE, y0).reshape(L.HEIGHT // 2, L.WIDTH).astype(np.float32)
    uv = np.frombuffer(buf, np.uint8, L.UV_SIZE, c0).reshape(L.HEIGHT // 4, L.WIDTH // 2, 2).astype(np.float32) - 128
    u = uv[..., 0].repeat(2, 0).repeat(2, 1)
    v = uv[..., 1].repeat(2, 0).repeat(2, 1)
    return np.clip(np.stack([y + 1.402 * v, y - 0.344136 * u - 0.714136 * v, y + 1.772 * u], -1), 0, 255).astype(np.uint8)


def render(main, which='normal'):
    """Decode a stored copy back to an 800x480 PIL image."""
    frame = np.empty((L.HEIGHT, L.WIDTH, 3), np.uint8)
    for parity, start in enumerate(L.COPIES[which]):
        frame[parity::2] = _field_rgb(main, start, start + L.Y_SIZE + L.PAD)
    return Image.fromarray(frame)


def render_framebuffer(fb):
    """Decode a Show_BG_Image framebuffer (one copy block, starting at the even field)."""
    even, odd = L.COPIES['normal']
    delta = odd - even                       # same layout for both copies
    frame = np.empty((L.HEIGHT, L.WIDTH, 3), np.uint8)
    for parity, start in enumerate((0, delta)):
        frame[parity::2] = _field_rgb(fb, start, start + L.Y_SIZE + L.PAD)
    return Image.fromarray(frame)
