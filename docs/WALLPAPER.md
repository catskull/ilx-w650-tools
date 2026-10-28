# The home-screen wallpaper

## Where it is

The wallpaper behind the home screen is stored inside `main_16.bin` twice:

| Copy | Even-line field | Odd-line field | Shown |
|---|---|---|---|
| normal | `0x637CB8` | `0x67E4B8` | home screen |
| dimmed | `0x6C50B8` | `0x70B8B8` | behind menus/overlays *(when exactly — unverified)* |

It is **not** a PNG or JPEG, which is why ordinary image carving does not find it.

## Format

Each copy is one **interlaced 800×480 frame** stored as two **800×240 fields** (even lines,
then odd lines). Each field is NV12:

| Part | Size | Content |
|---|---|---|
| Y plane | 192,000 bytes | luma, 800×240, one byte per pixel |
| pad | 512 bytes | zero |
| UV plane | 96,000 bytes | interleaved U,V pairs, 400×120 (2×2 subsampled) |

Fields of one copy are `0x46800` bytes apart (the remainder is padding). Colour is **full-range
BT.601**:

```
Y = 0.299 R + 0.587 G + 0.114 B
U = -0.168736 R - 0.331264 G + 0.5 B + 128
V = 0.5 R - 0.418688 G - 0.081312 B + 128
```

The dimmed copy is the normal one at **30 % brightness** (Y and U−128, V−128 scaled by ≈0.30 in the
stock firmware — equivalent to scaling RGB by 0.30).

Common mistakes when decoding it: treating each field as a full 800×480 frame (looks stretched
and "two-tunnelled"), or starting the Y plane 512 bytes late (the image wraps sideways with a seam
about 290 px from the left).

## How the firmware draws it

`Show_BG_Image(type)` at `main` `0x7ACEA` (it logs "Show_BG_Image") fills the video framebuffer:

| Type | Action |
|---|---|
| 3 | `memcpy(fb, 0x637CB8, 0x8D400)` — the normal copy (both fields + gaps) |
| 8 | `memcpy(fb, 0x6C50B8, 0x8D400)` — the dimmed copy |
| 9 | `memset` fills producing **solid black** (Y = `0x00`, UV = `0x80`) |

The data is copied raw into the display pipeline; nothing parses it, so any pixel values are safe.
Running this routine in the emulator (`ilxw650.emu.show_bg`) is part of verification; a black
wallpaper built by these tools is byte-identical to the firmware's own type-9 black background.

Whether a user setting selects type 9 (a built-in black background) has not been investigated.

## What the patcher does

1. Scale the source to 800×480 (`cover` = fill and crop, `contain` = fit inside with padding colour,
   `stretch`), compositing transparency onto the background colour.
2. Convert to full-range BT.601, split into even/odd fields, average chroma over 2×2 blocks.
3. Write Y and UV planes of all four fields (normal, and dimmed at ×0.30); leave pads and gaps.

The Python and JavaScript implementations differ only in rounding (≤ 1 level in a few pixels).
