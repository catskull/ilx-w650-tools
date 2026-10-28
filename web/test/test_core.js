// Node tests for web/src/fir_core.js.
// Usage: node web/test/test_core.js <Alpine_SYSTEM.FIR> [out dir]
// Writes js_black.FIR to the out dir so the Python suite can verify it with the device emulators.
const fs = require('fs');
const path = require('path');
const core = require('../src/fir_core.js');

const [firPath, outDir] = process.argv.slice(2);
if (!firPath) { console.error('usage: node test_core.js <Alpine_SYSTEM.FIR> [out dir]'); process.exit(2); }
const fir = new Uint8Array(fs.readFileSync(firPath));
let failures = 0;
const check = (name, ok) => { console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}`); if (!ok) failures++; };

// Decoder: every archive must decompress to data matching its RAR CRC.
for (const off of [0x2000, 0x40000, 0x180000]) {
  const a = core.parseArchive(fir, off);
  const t = Date.now();
  const data = core.unpack20(fir, a.dataOff, a.packSize, a.unpSize);
  check(`unpack20 ${a.name} (${Date.now() - t} ms)`, data.length === a.unpSize && core.crc32(data) === a.fileCrc);
}

const { main, info } = core.readFirmware(fir);
check('readFirmware accepts the image', main.length === 9499836);

// Black wallpaper: Y = 0 and UV = 128 everywhere in both copies.
const black = new Uint8Array(core.W * core.H * 4).fill(0);
for (let i = 3; i < black.length; i += 4) black[i] = 255;
const patched = core.patchWallpaper(main, black);
let planesOk = true;
for (const fields of Object.values(core.COPIES)) {
  for (const s of fields) {
    for (let i = s; i < s + 192000; i++) if (patched[i] !== 0) planesOk = false;
    for (let i = s + 192512; i < s + 192512 + 96000; i++) if (patched[i] !== 128) planesOk = false;
  }
}
check('black wallpaper planes are Y=0 / UV=128', planesOk);
check('build fingerprint unchanged by patching', core.maskedCrc(patched) === core.maskedCrc(main));

const t = Date.now();
const out = core.buildFirmware(fir, patched, info);   // throws if its own round-trip check fails
check(`buildFirmware round-trip (${Date.now() - t} ms)`, out.length === fir.length);
let rejected = false;
try { core.readFirmware(fir.subarray(0, 1000)); } catch (e) { rejected = true; }
check('rejects a truncated file', rejected);

if (outDir) {
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'js_black.FIR'), out);
}
process.exit(failures ? 1 : 0);
