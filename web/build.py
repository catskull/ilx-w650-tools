#!/usr/bin/env python3
"""Inline src/fir_core.js into src/template.html -> wallpaper_patcher.html (one self-contained file)."""
import pathlib

here = pathlib.Path(__file__).parent
core = (here / 'src' / 'fir_core.js').read_text()
assert '</script' not in core, 'core must not contain a closing script tag'
html = (here / 'src' / 'template.html').read_text().replace('/*FIR_CORE*/', core)
out = here / 'wallpaper_patcher.html'
out.write_text(html)
print(f'wrote {out} ({len(html)} bytes)')
