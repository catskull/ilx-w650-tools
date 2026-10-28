/*
 * iLX-W650 Alpine_SYSTEM.FIR wallpaper patcher — core logic (no DOM).
 *
 * Port of tools/rar20.py, build_flash.py and patch_wallpaper.py, plus a RAR 2.0
 * (Unpack20) decoder so the main firmware can be extracted from the .FIR in the browser.
 * Runs in a page, a Web Worker, or Node (module.exports).
 */
(function (root) {
  'use strict';

  // ------------------------------------------------------------------ constants

  const FLASH_SIZE = 0x800000;
  const MAIN_ARCHIVE = 0x180000;     // RAR archive holding main_16.bin
  const SLOT_END = 0x7F0000;         // first byte of the footer erase block
  const FOOTER_TAG = 0x7FFFC0;
  const FOOTER_SUM = 0x7FFFF0;

  const MAIN_NAME = 'main_16.bin';
  const MAIN_SIZE = 9499836;
  const MAIN_MASKED_CRC = 0x2B5C8407; // CRC32 of main_16.bin with the wallpaper planes zeroed

  const W = 800, H = 480;
  const YSZ = W * (H / 2), PAD = 512, CSZ = YSZ / 2;
  const COPIES = { normal: [0x637CB8, 0x67E4B8], dimmed: [0x6C50B8, 0x70B8B8] };
  const DIM_FACTOR = 0.30;            // measured from the stock dimmed copy

  // RAR 2.0 tables (unrar unpack20.cpp)
  const NC20 = 298, DC20 = 48, RC20 = 28, BC20 = 19, MC20 = 257;
  const LDECODE = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56,
    64, 80, 96, 112, 128, 160, 192, 224];
  const LBITS = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5];
  const DDECODE = [0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768,
    1024, 1536, 2048, 3072, 4096, 6144, 8192, 12288, 16384, 24576, 32768, 49152, 65536, 98304,
    131072, 196608, 262144, 327680, 393216, 458752, 524288, 589824, 655360, 720896, 786432,
    851968, 917504, 983040];
  const DBITS = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11,
    12, 12, 13, 13, 14, 14, 15, 15, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16];
  const SDDECODE = [0, 4, 8, 16, 32, 64, 128, 192];
  const SDBITS = [2, 2, 3, 4, 5, 6, 6, 6];

  const MAX_DIST = 0xFFFFF;          // stay inside the 1 MB window
  const BLOCK_SYMBOLS = 1 << 16;
  const MAX_CHAIN = 24;

  // ---------------------------------------------------------------------- helpers

  const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();

  function crc32(buf, start = 0, end = buf.length) {
    let c = 0xFFFFFFFF;
    for (let i = start; i < end; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  const u32be = (b, o) => ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0;
  const u16le = (b, o) => b[o] | (b[o + 1] << 8);
  const u32le = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
  function putU32be(b, o, v) { b[o] = v >>> 24; b[o + 1] = v >>> 16; b[o + 2] = v >>> 8; b[o + 3] = v; }
  function putU16le(b, o, v) { b[o] = v; b[o + 1] = v >>> 8; }
  function putU32le(b, o, v) { b[o] = v; b[o + 1] = v >>> 8; b[o + 2] = v >>> 16; b[o + 3] = v >>> 24; }
  const hex = (v, w = 8) => '0x' + (v >>> 0).toString(16).toUpperCase().padStart(w, '0');

  function byteSum(buf, end) {
    let s = 0;
    for (let i = 0; i < end; i++) s = (s + buf[i]) >>> 0;
    return s;
  }

  function farBonus(dist) {
    return (dist >= 0x2000 ? 1 : 0) + (dist >= 0x40000 ? 1 : 0);
  }

  // ------------------------------------------------------------- RAR container

  /** Parse the single-file RAR 1.5-4.x archive at `off`. */
  function parseArchive(buf, off) {
    if (buf[off] !== 0x52 || buf[off + 1] !== 0x61 || buf[off + 2] !== 0x72 || buf[off + 3] !== 0x21 ||
        buf[off + 4] !== 0x1A || buf[off + 5] !== 0x07 || buf[off + 6] !== 0x00) {
      throw new Error(`no RAR marker at ${hex(off)}`);
    }
    const mainSize = u16le(buf, off + 7 + 5);
    const h = off + 7 + mainSize;
    if (buf[h + 2] !== 0x74) throw new Error('first RAR block is not a file header');
    const hsize = u16le(buf, h + 5);
    const nameLen = u16le(buf, h + 26);
    const name = String.fromCharCode(...buf.subarray(h + 32, h + 32 + nameLen));
    const hcrc = u16le(buf, h);
    return {
      off, headerOff: h, hsize, name,
      flags: u16le(buf, h + 3),
      packSize: u32le(buf, h + 7),
      unpSize: u32le(buf, h + 11),
      fileCrc: u32le(buf, h + 16),
      unpVer: buf[h + 24],
      method: buf[h + 25],
      dataOff: h + hsize,
      headerCrcOk: (crc32(buf, h + 2, h + hsize) & 0xFFFF) === hcrc,
    };
  }

  /** Rebuild the archive from the template's headers with new contents. */
  function buildArchive(template, info, data, packed) {
    const headLen = info.dataOff - info.off;
    const out = new Uint8Array(headLen + packed.length);
    out.set(template.subarray(info.off, info.dataOff), 0);
    const h = info.headerOff - info.off;
    putU32le(out, h + 7, packed.length);
    putU32le(out, h + 11, data.length);
    putU32le(out, h + 16, crc32(data));
    putU16le(out, h, crc32(out, h + 2, h + info.hsize) & 0xFFFF);
    out.set(packed, headLen);
    return out;
  }

  // ------------------------------------------------------------ Unpack20 decoder

  function makeDecode(lens, off, size) {
    const count = new Int32Array(16);
    for (let i = 0; i < size; i++) count[lens[off + i] & 15]++;
    count[0] = 0;
    const dlen = new Int32Array(16), dpos = new Int32Array(16), num = new Uint16Array(size);
    let upper = 0;
    for (let i = 1; i < 16; i++) {
      upper += count[i];
      dlen[i] = upper << (16 - i);
      upper *= 2;
      dpos[i] = dpos[i - 1] + count[i - 1];
    }
    const cp = dpos.slice();
    for (let i = 0; i < size; i++) {
      const b = lens[off + i] & 15;
      if (b) num[cp[b]++] = i;
    }
    return { dlen, dpos, num, size };
  }

  /** Decode a RAR 2.0 stream (unrar's Unpack20, including audio blocks). */
  function unpack20(src, start, packSize, unpSize) {
    const inb = new Uint8Array(packSize + 16);
    inb.set(src.subarray(start, start + packSize));
    const bitEnd = packSize * 8 + 64;
    let bp = 0;
    const getbits = () => {
      const i = bp >>> 3;
      return (((inb[i] << 16) | (inb[i + 1] << 8) | inb[i + 2]) >>> (8 - (bp & 7))) & 0xFFFF;
    };
    const addbits = (n) => { bp += n; };
    const decodeNumber = (t) => {
      const bf = getbits() & 0xFFFE;
      let n = 1;
      while (n < 15 && bf >= t.dlen[n]) n++;
      addbits(n);
      let pos = t.dpos[n] + ((bf - t.dlen[n - 1]) >>> (16 - n));
      if (pos >= t.size) pos = 0;
      return t.num[pos];
    };

    const out = new Uint8Array(unpSize);
    let op = 0;
    const oldTable = new Uint8Array(MC20 * 4);
    const oldDist = [0, 0, 0, 0];
    let oldDistPtr = 0, lastDist = 0, lastLength = 0;
    let audio = false, channels = 1, curCh = 0, chDelta = 0;
    const audV = Array.from({ length: 4 }, () => ({
      K1: 0, K2: 0, K3: 0, K4: 0, K5: 0, D1: 0, D2: 0, D3: 0, D4: 0,
      LastDelta: 0, Dif: new Array(11).fill(0), ByteCount: 0, LastChar: 0,
    }));
    let LD, DD, RD;
    const MD = [];

    function readTables() {
      const bf = getbits();
      audio = (bf & 0x8000) !== 0;
      if (!(bf & 0x4000)) oldTable.fill(0);
      addbits(2);
      let tableSize;
      if (audio) {
        channels = ((bf >>> 12) & 3) + 1;
        if (curCh >= channels) curCh = 0;
        addbits(2);
        tableSize = MC20 * channels;
      } else {
        tableSize = NC20 + DC20 + RC20;
      }
      const bl = new Uint8Array(BC20);
      for (let i = 0; i < BC20; i++) { bl[i] = getbits() >>> 12; addbits(4); }
      const BD = makeDecode(bl, 0, BC20);
      const table = new Uint8Array(MC20 * 4);
      for (let i = 0; i < tableSize;) {
        if (bp > bitEnd) return false;
        const num = decodeNumber(BD);
        if (num < 16) {
          table[i] = (num + oldTable[i]) & 15; i++;
        } else if (num === 16) {
          let n = (getbits() >>> 14) + 3; addbits(2);
          if (i === 0) return false;
          while (n-- > 0 && i < tableSize) { table[i] = table[i - 1]; i++; }
        } else {
          let n;
          if (num === 17) { n = (getbits() >>> 13) + 3; addbits(3); } else { n = (getbits() >>> 9) + 11; addbits(7); }
          while (n-- > 0 && i < tableSize) table[i++] = 0;
        }
      }
      if (audio) {
        for (let c = 0; c < channels; c++) MD[c] = makeDecode(table, c * MC20, MC20);
      } else {
        LD = makeDecode(table, 0, NC20);
        DD = makeDecode(table, NC20, DC20);
        RD = makeDecode(table, NC20 + DC20, RC20);
      }
      oldTable.set(table.subarray(0, tableSize));
      return true;
    }

    function decodeAudio(delta) {
      const v = audV[curCh];
      v.ByteCount++;
      v.D4 = v.D3; v.D3 = v.D2; v.D2 = v.LastDelta - v.D1; v.D1 = v.LastDelta;
      let pch = 8 * v.LastChar + v.K1 * v.D1 + v.K2 * v.D2 + v.K3 * v.D3 + v.K4 * v.D4 + v.K5 * chDelta;
      pch = (pch >> 3) & 0xFF;
      const ch = (pch - delta) | 0;
      const d = ((delta << 24) >> 24) << 3;
      const dif = v.Dif;
      dif[0] += Math.abs(d);
      dif[1] += Math.abs(d - v.D1); dif[2] += Math.abs(d + v.D1);
      dif[3] += Math.abs(d - v.D2); dif[4] += Math.abs(d + v.D2);
      dif[5] += Math.abs(d - v.D3); dif[6] += Math.abs(d + v.D3);
      dif[7] += Math.abs(d - v.D4); dif[8] += Math.abs(d + v.D4);
      dif[9] += Math.abs(d - chDelta); dif[10] += Math.abs(d + chDelta);
      chDelta = v.LastDelta = (((ch - v.LastChar) << 24) >> 24);
      v.LastChar = ch;
      if ((v.ByteCount & 0x1F) === 0) {
        let minDif = dif[0], numMin = 0;
        dif[0] = 0;
        for (let i = 1; i < 11; i++) {
          if (dif[i] < minDif) { minDif = dif[i]; numMin = i; }
          dif[i] = 0;
        }
        switch (numMin) {
          case 1: if (v.K1 >= -16) v.K1--; break;
          case 2: if (v.K1 < 16) v.K1++; break;
          case 3: if (v.K2 >= -16) v.K2--; break;
          case 4: if (v.K2 < 16) v.K2++; break;
          case 5: if (v.K3 >= -16) v.K3--; break;
          case 6: if (v.K3 < 16) v.K3++; break;
          case 7: if (v.K4 >= -16) v.K4--; break;
          case 8: if (v.K4 < 16) v.K4++; break;
          case 9: if (v.K5 >= -16) v.K5--; break;
          case 10: if (v.K5 < 16) v.K5++; break;
        }
      }
      return ch & 0xFF;
    }

    function copyString(length, dist) {
      lastDist = oldDist[oldDistPtr++ & 3] = dist;
      lastLength = length;
      if (dist > op) throw new Error(`match distance ${dist} before start of output at ${op}`);
      for (let k = 0; k < length && op < unpSize; k++, op++) out[op] = out[op - dist];
    }

    if (!readTables()) throw new Error('bad initial Huffman tables');
    while (op < unpSize) {
      if (bp > bitEnd) throw new Error(`compressed data ended early (${op} of ${unpSize} bytes)`);
      if (audio) {
        const a = decodeNumber(MD[curCh]);
        if (a === 256) { if (!readTables()) throw new Error('bad Huffman tables'); continue; }
        out[op++] = decodeAudio(a);
        if (++curCh === channels) curCh = 0;
        continue;
      }
      let num = decodeNumber(LD);
      if (num < 256) { out[op++] = num; continue; }
      if (num > 269) {
        num -= 270;
        let length = LDECODE[num] + 3;
        let bits = LBITS[num];
        if (bits) { length += getbits() >>> (16 - bits); addbits(bits); }
        const dn = decodeNumber(DD);
        let dist = DDECODE[dn] + 1;
        bits = DBITS[dn];
        if (bits) { dist += getbits() >>> (16 - bits); addbits(bits); }
        if (dist >= 0x2000) { length++; if (dist >= 0x40000) length++; }
        copyString(length, dist);
        continue;
      }
      if (num === 269) { if (!readTables()) throw new Error('bad Huffman tables'); continue; }
      if (num === 256) { copyString(lastLength, lastDist); continue; }
      if (num < 261) {
        const dist = oldDist[(oldDistPtr - (num - 256)) & 3];
        const ln = decodeNumber(RD);
        let length = LDECODE[ln] + 2;
        const bits = LBITS[ln];
        if (bits) { length += getbits() >>> (16 - bits); addbits(bits); }
        if (dist >= 0x101) { length++; if (dist >= 0x2000) { length++; if (dist >= 0x40000) length++; } }
        copyString(length, dist);
        continue;
      }
      num -= 261;
      let dist = SDDECODE[num] + 1;
      const bits = SDBITS[num];
      if (bits) { dist += getbits() >>> (16 - bits); addbits(bits); }
      copyString(2, dist);
    }
    return out;
  }

  // ------------------------------------------------------------ RAR 2.0 encoder

  /** Huffman code lengths limited to 15 bits (frequency flattening). */
  function codeLengths(freqs, limit = 15) {
    let f = Array.from(freqs);
    for (;;) {
      const lengths = new Uint8Array(f.length);
      const used = [];
      for (let s = 0; s < f.length; s++) if (f[s]) used.push(s);
      if (!used.length) return lengths;
      if (used.length === 1) { lengths[used[0]] = 1; return lengths; }
      const weight = [], parent = [];
      const heap = [];
      const push = (id) => {
        heap.push(id);
        let i = heap.length - 1;
        while (i > 0) {
          const p = (i - 1) >> 1;
          if (weight[heap[p]] < weight[heap[i]] || (weight[heap[p]] === weight[heap[i]] && heap[p] < heap[i])) break;
          [heap[p], heap[i]] = [heap[i], heap[p]]; i = p;
        }
      };
      const pop = () => {
        const top = heap[0], last = heap.pop();
        if (heap.length) {
          heap[0] = last;
          let i = 0;
          for (;;) {
            const l = 2 * i + 1, r = l + 1;
            let m = i;
            const less = (a, b) => weight[heap[a]] < weight[heap[b]] || (weight[heap[a]] === weight[heap[b]] && heap[a] < heap[b]);
            if (l < heap.length && less(l, m)) m = l;
            if (r < heap.length && less(r, m)) m = r;
            if (m === i) break;
            [heap[m], heap[i]] = [heap[i], heap[m]]; i = m;
          }
        }
        return top;
      };
      used.forEach((s, k) => { weight[k] = f[s]; parent[k] = -1; push(k); });
      while (heap.length > 1) {
        const a = pop(), b = pop();
        const c = weight.length;
        weight[c] = weight[a] + weight[b]; parent[c] = -1;
        parent[a] = parent[b] = c;
        push(c);
      }
      let maxLen = 0;
      used.forEach((s, k) => {
        let d = 0;
        for (let n = k; parent[n] !== -1; n = parent[n]) d++;
        lengths[s] = d;
        if (d > maxLen) maxLen = d;
      });
      if (maxLen <= limit) return lengths;
      f = f.map((x) => (x ? (x >> 1) | 1 : 0));
    }
  }

  /** Canonical codes in the order unrar's MakeDecodeTables assigns them. */
  function canonicalCodes(lengths) {
    const codes = new Uint32Array(lengths.length);
    let code = 0;
    for (let bits = 1; bits < 16; bits++) {
      for (let s = 0; s < lengths.length; s++) if (lengths[s] === bits) codes[s] = code++;
      code <<= 1;
    }
    return codes;
  }

  class BitWriter {
    constructor(cap) { this.buf = new Uint8Array(cap); this.n = 0; this.acc = 0; this.nbits = 0; }
    write(value, bits) {
      if (!bits) return;
      this.acc = (this.acc << bits) | value;
      this.nbits += bits;
      while (this.nbits >= 8) {
        this.nbits -= 8;
        if (this.n === this.buf.length) {
          const g = new Uint8Array(this.buf.length * 2); g.set(this.buf); this.buf = g;
        }
        this.buf[this.n++] = (this.acc >>> this.nbits) & 0xFF;
      }
      this.acc &= (1 << this.nbits) - 1;
    }
    finish(padBytes) {
      if (this.nbits) this.write(0, 8 - this.nbits);
      const out = new Uint8Array(this.n + padBytes);
      out.set(this.buf.subarray(0, this.n));
      return out;
    }
  }

  function lenSlot(v) {        // encoded length value 0..255 -> [code index, bits, extra]
    let i = LDECODE.length - 1;
    while (LDECODE[i] > v) i--;
    return [i, LBITS[i], v - LDECODE[i]];
  }

  function distSlot(d) {       // distance-1 -> [code index, bits, extra]
    let lo = 0, hi = DC20 - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (DDECODE[mid] <= d) lo = mid; else hi = mid - 1;
    }
    return [lo, DBITS[lo], d - DDECODE[lo]];
  }

  /** Greedy hash-chain LZ77. Returns token arrays (len 0 = literal stored in dist). */
  function lz77(data, onProgress) {
    const n = data.length;
    const HB = 1 << 20;
    const head = new Int32Array(HB).fill(-1);
    const prev = new Int32Array(n);
    const tLen = new Uint16Array(n);
    const tVal = new Int32Array(n);
    let count = 0;
    const hash = (i) => (Math.imul((data[i] << 16) | (data[i + 1] << 8) | data[i + 2], 0x9E3779B1) >>> 12) & (HB - 1);
    let i = 0, nextReport = 0;
    while (i < n) {
      if (onProgress && i >= nextReport) { onProgress(i / n); nextReport = i + (1 << 18); }
      let bestLen = 0, bestDist = 0;
      if (i + 3 <= n) {
        const h = hash(i);
        let cand = head[h], chain = MAX_CHAIN;
        const limit = Math.min(258, n - i);
        while (cand >= 0 && chain-- > 0 && i - cand <= MAX_DIST) {
          if (data[cand] === data[i] && data[cand + 1] === data[i + 1] && data[cand + 2] === data[i + 2] &&
              (bestLen === 0 || data[cand + bestLen] === data[i + bestLen])) {
            let l = 3;
            while (l < limit && data[cand + l] === data[i + l]) l++;
            if (l > bestLen) { bestLen = l; bestDist = i - cand; if (l === limit) break; }
          }
          cand = prev[cand];
        }
        prev[i] = head[h];
        head[h] = i;
      }
      if (bestLen >= 3 + farBonus(bestDist)) {
        tLen[count] = bestLen; tVal[count] = bestDist; count++;
        const stop = Math.min(i + bestLen, n - 3, i + 8);
        for (let j = i + 1; j < stop; j++) {
          const h = hash(j);
          prev[j] = head[h];
          head[h] = j;
        }
        i += bestLen;
      } else {
        tLen[count] = 0; tVal[count] = data[i]; count++;
        i++;
      }
    }
    return { tLen, tVal, count };
  }

  function writeTables(bw, table) {
    bw.write(0, 2);              // not audio, discard the previous table
    const items = [];
    for (let i = 0; i < table.length;) {
      if (table[i] === 0) {
        let j = i;
        while (j < table.length && table[j] === 0 && j - i < 138) j++;
        const run = j - i;
        if (run >= 11) { items.push([18, 7, run - 11]); i = j; continue; }
        if (run >= 3) { items.push([17, 3, run - 3]); i = j; continue; }
      }
      items.push([table[i], 0, 0]);
      i++;
    }
    const bfreq = new Array(BC20).fill(0);
    for (const [s] of items) bfreq[s]++;
    const blen = codeLengths(bfreq);
    const bcode = canonicalCodes(blen);
    for (let s = 0; s < BC20; s++) bw.write(blen[s], 4);
    for (const [s, eb, ev] of items) { bw.write(bcode[s], blen[s]); bw.write(ev, eb); }
  }

  /** Compress data as a raw RAR 2.0 stream. */
  function compress20(data, onProgress) {
    const { tLen, tVal, count } = lz77(data, onProgress && ((f) => onProgress(f * 0.85)));
    // Pre-compute symbols for every token.
    const sym = new Uint16Array(count);
    const lb = new Uint8Array(count), lx = new Uint16Array(count);
    const di = new Uint8Array(count), db = new Uint8Array(count), dx = new Uint32Array(count);
    for (let k = 0; k < count; k++) {
      if (!tLen[k]) { sym[k] = tVal[k]; continue; }
      const dist = tVal[k];
      const [li, lbits, lextra] = lenSlot(tLen[k] - farBonus(dist) - 3);
      const [dI, dbits, dextra] = distSlot(dist - 1);
      sym[k] = 270 + li; lb[k] = lbits; lx[k] = lextra; di[k] = dI; db[k] = dbits; dx[k] = dextra;
    }
    const bw = new BitWriter(data.length >> 1);
    const nBlocks = Math.max(1, Math.ceil(count / BLOCK_SYMBOLS));
    for (let b = 0; b < nBlocks; b++) {
      if (onProgress) onProgress(0.85 + 0.15 * b / nBlocks);
      const s0 = b * BLOCK_SYMBOLS, s1 = Math.min(count, s0 + BLOCK_SYMBOLS);
      const last = b === nBlocks - 1;
      const lf = new Array(NC20).fill(0), df = new Array(DC20).fill(0), rf = new Array(RC20).fill(0);
      for (let k = s0; k < s1; k++) { lf[sym[k]]++; if (sym[k] >= 270) df[di[k]]++; }
      if (!last) lf[269]++;
      rf[0] = 1;                  // valid but unused repeat-length table
      const ll = codeLengths(lf), dl = codeLengths(df), rl = codeLengths(rf);
      if (!dl.some((x) => x)) dl[0] = 1;
      const lc = canonicalCodes(ll), dc = canonicalCodes(dl);
      const table = new Uint8Array(NC20 + DC20 + RC20);
      table.set(ll, 0); table.set(dl, NC20); table.set(rl, NC20 + DC20);
      writeTables(bw, table);
      for (let k = s0; k < s1; k++) {
        const s = sym[k];
        bw.write(lc[s], ll[s]);
        if (s >= 270) {
          bw.write(lx[k], lb[k]);
          bw.write(dc[di[k]], dl[di[k]]);
          bw.write(dx[k], db[k]);
        }
      }
      if (!last) bw.write(lc[269], ll[269]);
    }
    return bw.finish(4);        // the decoder reads a few bytes ahead
  }

  // ------------------------------------------------------------------ wallpaper

  /** Full-range BT.601 RGBA (one field, 800x240) -> Y plane + interleaved UV plane. */
  function fieldToNV12(rgba, fieldParity, scale) {
    const y = new Uint8Array(YSZ), uv = new Uint8Array(CSZ);
    const clamp = (v) => (v < 0 ? 0 : v > 255 ? 255 : Math.round(v));
    const uAcc = new Float64Array(CSZ / 2), vAcc = new Float64Array(CSZ / 2);
    for (let fr = 0; fr < H / 2; fr++) {
      const row = fr * 2 + fieldParity;
      for (let x = 0; x < W; x++) {
        const p = (row * W + x) * 4;
        const r = rgba[p] * scale, g = rgba[p + 1] * scale, b = rgba[p + 2] * scale;
        y[fr * W + x] = clamp(0.299 * r + 0.587 * g + 0.114 * b);
        const c = (fr >> 1) * (W / 2) + (x >> 1);
        uAcc[c] += -0.168736 * r - 0.331264 * g + 0.5 * b + 128;
        vAcc[c] += 0.5 * r - 0.418688 * g - 0.081312 * b + 128;
      }
    }
    for (let c = 0; c < CSZ / 2; c++) { uv[2 * c] = clamp(uAcc[c] / 4); uv[2 * c + 1] = clamp(vAcc[c] / 4); }
    return { y, uv };
  }

  /** Write both wallpaper copies (normal + dimmed) from an 800x480 RGBA image into main. */
  function patchWallpaper(main, rgba) {
    if (rgba.length !== W * H * 4) throw new Error(`image must be ${W}x${H} RGBA`);
    const out = new Uint8Array(main);
    for (const [name, fields] of Object.entries(COPIES)) {
      const scale = name === 'dimmed' ? DIM_FACTOR : 1;
      fields.forEach((start, parity) => {
        for (let i = start + YSZ; i < start + YSZ + PAD; i++) {
          if (out[i] !== 0) throw new Error(`unexpected data in pad at ${hex(i)}`);
        }
        const { y, uv } = fieldToNV12(rgba, parity, scale);
        out.set(y, start);
        out.set(uv, start + YSZ + PAD);
      });
    }
    return out;
  }

  /** Render one stored wallpaper copy back to 800x480 RGBA (what the unit will show). */
  function renderWallpaper(main, which) {
    const rgba = new Uint8ClampedArray(W * H * 4);
    COPIES[which].forEach((start, parity) => {
      for (let fr = 0; fr < H / 2; fr++) {
        const row = fr * 2 + parity;
        for (let x = 0; x < W; x++) {
          const yv = main[start + fr * W + x];
          const c = start + YSZ + PAD + (fr >> 1) * W + (x & ~1);
          const u = main[c] - 128, v = main[c + 1] - 128;
          const p = (row * W + x) * 4;
          rgba[p] = yv + 1.402 * v;
          rgba[p + 1] = yv - 0.344136 * u - 0.714136 * v;
          rgba[p + 2] = yv + 1.772 * u;
          rgba[p + 3] = 255;
        }
      }
    });
    return rgba;
  }

  function maskedCrc(main) {
    const m = new Uint8Array(main);
    for (const fields of Object.values(COPIES)) {
      for (const s of fields) { m.fill(0, s, s + YSZ); m.fill(0, s + YSZ + PAD, s + YSZ + PAD + CSZ); }
    }
    return crc32(m);
  }

  // --------------------------------------------------------------- flash image

  /** Validate a .FIR and extract main_16.bin. Throws with a readable reason. */
  function readFirmware(fir, log = () => {}) {
    if (fir.length !== FLASH_SIZE) throw new Error(`file is ${fir.length} bytes; an Alpine_SYSTEM.FIR is exactly ${FLASH_SIZE}`);
    const tag = String.fromCharCode(...fir.subarray(FOOTER_TAG, FOOTER_TAG + 14));
    if (tag !== 'ALPINE_ILX_SYS') throw new Error(`footer tag is "${tag}", expected ALPINE_ILX_SYS`);
    const sum = byteSum(fir, FOOTER_SUM);
    if (sum !== u32be(fir, FOOTER_SUM)) throw new Error('footer checksum does not match; the file is damaged');
    log(`Footer tag ${tag}, checksum ${hex(sum)} OK`);
    const archLen = u32be(fir, MAIN_ARCHIVE - 4);
    const info = parseArchive(fir, MAIN_ARCHIVE);
    if (info.name !== MAIN_NAME) throw new Error(`archive at ${hex(MAIN_ARCHIVE)} holds "${info.name}", expected ${MAIN_NAME}`);
    if (info.unpVer !== 20 || info.method !== 0x33) throw new Error(`unexpected RAR version ${info.unpVer} / method ${hex(info.method, 2)}`);
    if (!info.headerCrcOk) throw new Error('RAR file header CRC mismatch');
    if (info.dataOff + info.packSize - MAIN_ARCHIVE !== archLen) throw new Error('archive length prefix does not match its headers');
    if (info.unpSize !== MAIN_SIZE) throw new Error(`${MAIN_NAME} is ${info.unpSize} bytes; this tool only knows the ${MAIN_SIZE}-byte version`);
    const main = unpack20(fir, info.dataOff, info.packSize, info.unpSize);
    if (crc32(main) !== info.fileCrc) throw new Error(`${MAIN_NAME} CRC mismatch after decompression`);
    log(`Extracted ${MAIN_NAME}: ${main.length} bytes, CRC ${hex(info.fileCrc)} OK`);
    const fp = maskedCrc(main);
    if (fp !== MAIN_MASKED_CRC) throw new Error(`unrecognised firmware build (fingerprint ${hex(fp)}); the wallpaper offsets may differ`);
    log(`Firmware build recognised (fingerprint ${hex(fp)})`);
    return { main, info };
  }

  /** Rebuild the 8 MiB image with a new main_16.bin, then verify the result. */
  function buildFirmware(fir, main, info, log = () => {}, progress = () => {}) {
    const packed = compress20(main, progress);
    const archive = buildArchive(fir, info, main, packed);
    log(`Compressed ${MAIN_NAME}: ${main.length} -> ${archive.length} bytes (stock archive ${u32be(fir, MAIN_ARCHIVE - 4)})`);
    if (MAIN_ARCHIVE + archive.length > SLOT_END) throw new Error(`archive too large: ends at ${hex(MAIN_ARCHIVE + archive.length)}`);

    const out = new Uint8Array(fir);
    out.fill(0xFF, MAIN_ARCHIVE, SLOT_END);
    putU32be(out, MAIN_ARCHIVE - 4, archive.length);
    out.set(archive, MAIN_ARCHIVE);
    putU32be(out, FOOTER_SUM, byteSum(out, FOOTER_SUM));

    // Independent checks on the finished image.
    for (let i = 0; i < MAIN_ARCHIVE - 4; i++) if (out[i] !== fir[i]) throw new Error(`boot area changed at ${hex(i)}`);
    for (let i = SLOT_END; i < FOOTER_SUM; i++) if (out[i] !== fir[i]) throw new Error(`footer area changed at ${hex(i)}`);
    const check = parseArchive(out, MAIN_ARCHIVE);
    if (!check.headerCrcOk) throw new Error('rebuilt RAR header CRC mismatch');
    const back = unpack20(out, check.dataOff, check.packSize, check.unpSize);
    if (back.length !== main.length || crc32(back) !== check.fileCrc) throw new Error('rebuilt archive does not decompress to the patched firmware');
    for (let i = 0; i < main.length; i++) if (back[i] !== main[i]) throw new Error(`round-trip mismatch at ${hex(i)}`);
    if (byteSum(out, FOOTER_SUM) !== u32be(out, FOOTER_SUM)) throw new Error('footer checksum wrong');
    log(`Verified: archive decompresses to the patched ${MAIN_NAME} (CRC ${hex(check.fileCrc)}), boot/recovery/pre_main untouched, footer checksum ${hex(u32be(out, FOOTER_SUM))}`);
    return out;
  }

  const api = {
    W, H, COPIES, DIM_FACTOR, crc32, hex,
    parseArchive, unpack20, compress20, buildArchive,
    patchWallpaper, renderWallpaper, maskedCrc, readFirmware, buildFirmware,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FirCore = api;
})(typeof self !== 'undefined' ? self : this);
