"""Minimal RAR 2.0 (unpack version 20, method 0x33) compressor.

Produces the same stream format that unrar's Unpack20 (and the iLX-W650 boot
chain) decodes: an LZ77 parse coded with per-block canonical Huffman tables.
It only emits literals, new matches (codes 270-297) and table switches (269);
the optional repeat and short-distance codes are never needed for correctness.
"""
import heapq
import struct
import zlib

NC20, DC20, RC20, BC20 = 298, 48, 28, 19

LDECODE = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56,
           64, 80, 96, 112, 128, 160, 192, 224]
LBITS = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4,
         5, 5, 5, 5]
DDECODE = [0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384,
           512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192, 12288, 16384,
           24576, 32768, 49152, 65536, 98304, 131072, 196608, 262144, 327680,
           393216, 458752, 524288, 589824, 655360, 720896, 786432, 851968,
           917504, 983040]
DBITS = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10,
         11, 11, 12, 12, 13, 13, 14, 14, 15, 15, 16, 16, 16, 16, 16, 16, 16, 16,
         16, 16, 16, 16, 16, 16]

MAX_DIST = 0xFFFFF          # stay inside the 1 MB window
MAX_CODE_LEN = 15
BLOCK_SYMBOLS = 1 << 16     # symbols per Huffman block

# Precomputed reverse lookups: value -> (code index, extra bits, extra value).
_LEN_SLOT = []
for _i, (_base, _bits) in enumerate(zip(LDECODE, LBITS)):
    for _extra in range(1 << _bits):
        _LEN_SLOT.append((_i, _bits, _extra))


def _dist_slot(d):
    """Return (code index, extra bit count, extra value) for a distance-1 value."""
    lo, hi = 0, DC20 - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if DDECODE[mid] <= d:
            lo = mid
        else:
            hi = mid - 1
    return lo, DBITS[lo], d - DDECODE[lo]


def far_bonus(dist):
    """Extra length the decoder silently adds to a new match at this distance."""
    return (dist >= 0x2000) + (dist >= 0x40000)


# --------------------------------------------------------------------------- LZ77

def lz77(data, min_len=3, max_chain=24):
    """Greedy hash-chain parse. Yields ('L', byte) or ('M', length, distance)."""
    n = len(data)
    head = {}
    prev = [0] * n
    out = []
    i = 0
    while i < n:
        best_len, best_dist = 0, 0
        if i + 3 <= n:
            key = data[i:i + 3]
            cand = head.get(key, -1)
            chain = max_chain
            limit = min(258, n - i)
            while cand >= 0 and chain and i - cand <= MAX_DIST:
                if data[cand + best_len] == data[i + best_len] if i + best_len < n else False:
                    length = 3
                    # Extend in 32-byte strides, then byte by byte.
                    while length + 32 <= limit and data[cand + length:cand + length + 32] == data[i + length:i + length + 32]:
                        length += 32
                    while length < limit and data[cand + length] == data[i + length]:
                        length += 1
                    if length > best_len:
                        best_len, best_dist = length, i - cand
                        if length == limit:
                            break
                cand = prev[cand] if cand else -1
                chain -= 1
            prev[i] = head.get(key, -1)
            head[key] = i
        dist = best_dist
        # Far matches need a longer minimum because the decoder adds a bonus.
        need = min_len + far_bonus(dist)
        if best_len >= need and best_len - far_bonus(dist) - 3 <= 255:
            out.append(('M', best_len, dist))
            # Insert a few positions inside the match to keep the chains useful.
            stop = min(i + best_len, n - 3)
            j = i + 1
            while j < stop and j < i + 8:
                key = data[j:j + 3]
                prev[j] = head.get(key, -1)
                head[key] = j
                j += 1
            i += best_len
        else:
            out.append(('L', data[i]))
            i += 1
    return out


# ------------------------------------------------------------------------ Huffman

def code_lengths(freqs, limit=MAX_CODE_LEN):
    """Huffman code lengths with a maximum length, by frequency flattening."""
    freqs = list(freqs)
    while True:
        used = [(f, s) for s, f in enumerate(freqs) if f]
        lengths = [0] * len(freqs)
        if not used:
            return lengths
        if len(used) == 1:
            lengths[used[0][1]] = 1
            return lengths
        heap = [(f, k, [s]) for k, (f, s) in enumerate(used)]
        heapq.heapify(heap)
        tie = len(heap)
        while len(heap) > 1:
            f1, _, s1 = heapq.heappop(heap)
            f2, _, s2 = heapq.heappop(heap)
            for s in s1 + s2:
                lengths[s] += 1
            heapq.heappush(heap, (f1 + f2, tie, s1 + s2))
            tie += 1
        if max(lengths) <= limit:
            return lengths
        freqs = [(f >> 1) | 1 if f else 0 for f in freqs]


def canonical_codes(lengths):
    """Codes assigned the way unrar's MakeDecodeTables expects (MSB first)."""
    codes = [0] * len(lengths)
    code = 0
    for bits in range(1, 16):
        for sym, ln in enumerate(lengths):
            if ln == bits:
                codes[sym] = code
                code += 1
        code <<= 1
    return codes


# ---------------------------------------------------------------------- Bit output

class BitWriter:
    def __init__(self):
        self.buf = bytearray()
        self.acc = 0
        self.nbits = 0

    def write(self, value, bits):
        if not bits:
            return
        self.acc = (self.acc << bits) | value
        self.nbits += bits
        while self.nbits >= 8:
            self.nbits -= 8
            self.buf.append((self.acc >> self.nbits) & 0xFF)
        self.acc &= (1 << self.nbits) - 1

    def flush(self):
        if self.nbits:
            self.buf.append((self.acc << (8 - self.nbits)) & 0xFF)
            self.acc = self.nbits = 0
        return bytes(self.buf)


# ------------------------------------------------------------------------ Encoder

def _tokens_to_symbols(tokens):
    """Map LZ tokens to (main symbol, extras...) tuples."""
    syms = []
    for t in tokens:
        if t[0] == 'L':
            syms.append((t[1],))
        else:
            _, length, dist = t
            li, lb, lx = _LEN_SLOT[length - far_bonus(dist) - 3]
            di, db, dx = _dist_slot(dist - 1)
            syms.append((270 + li, lb, lx, di, db, dx))
    return syms


def _write_tables(bw, table):
    """ReadTables20 header: no audio, discard old table, then bit-length code."""
    bw.write(0, 2)
    # Run-length code the 374 lengths with symbols 0-15, 17 (3-10 zeros), 18 (11-138 zeros).
    items = []
    i = 0
    while i < len(table):
        if table[i] == 0:
            j = i
            while j < len(table) and table[j] == 0 and j - i < 138:
                j += 1
            run = j - i
            if run >= 11:
                items.append((18, 7, run - 11))
                i = j
                continue
            if run >= 3:
                items.append((17, 3, run - 3))
                i = j
                continue
        items.append((table[i], 0, 0))
        i += 1
    bfreq = [0] * BC20
    for s, _, _ in items:
        bfreq[s] += 1
    blen = code_lengths(bfreq)
    bcode = canonical_codes(blen)
    for ln in blen:
        bw.write(ln, 4)
    for s, eb, ev in items:
        bw.write(bcode[s], blen[s])
        bw.write(ev, eb)


def compress(data, max_chain=24):
    """Return a raw RAR 2.0 compressed stream for data."""
    syms = _tokens_to_symbols(lz77(data, max_chain=max_chain))
    bw = BitWriter()
    blocks = [syms[i:i + BLOCK_SYMBOLS] for i in range(0, len(syms), BLOCK_SYMBOLS)] or [[]]
    for bi, block in enumerate(blocks):
        last = bi == len(blocks) - 1
        lf, df = [0] * NC20, [0] * DC20
        for s in block:
            lf[s[0]] += 1
            if len(s) > 1:
                df[s[3]] += 1
        if not last:
            lf[269] += 1                       # switch to the next block's tables
        rf = [0] * RC20
        rf[0] = 1                              # keep a valid (unused) repeat table
        ll, dl, rl = code_lengths(lf), code_lengths(df), code_lengths(rf)
        if not any(dl):
            dl[0] = 1
        lc, dc = canonical_codes(ll), canonical_codes(dl)
        _write_tables(bw, ll + dl + rl)
        for s in block:
            bw.write(lc[s[0]], ll[s[0]])
            if len(s) > 1:
                _, lb, lx, di, db, dx = s
                bw.write(lx, lb)
                bw.write(dc[di], dl[di])
                bw.write(dx, db)
        if not last:
            bw.write(lc[269], ll[269])
    # The decoder reads a few bytes ahead of what it consumes.
    return bw.flush() + b'\0' * 4


# ------------------------------------------------------------------- RAR framing

def build_archive(template, name, data, packed):
    """Rebuild a single-file RAR 1.5-4.x archive, reusing the template's headers."""
    marker, main = template[:7], template[7:20]
    hsize = struct.unpack('<H', template[20 + 5:20 + 7])[0]
    fh = bytearray(template[20:20 + hsize])
    assert fh[2] == 0x74 and fh[24:26] == bytes([20, 0x33]), 'unexpected file header'
    assert fh[32:32 + len(name)] == name.encode(), 'name mismatch'
    struct.pack_into('<I', fh, 7, len(packed))
    struct.pack_into('<I', fh, 11, len(data))
    struct.pack_into('<I', fh, 16, zlib.crc32(data) & 0xFFFFFFFF)
    struct.pack_into('<H', fh, 0, zlib.crc32(bytes(fh[2:])) & 0xFFFF)
    return marker + main + bytes(fh) + packed
