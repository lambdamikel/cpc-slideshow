#!/usr/bin/env python3
"""Build an Amstrad CPC disk, as both .dsk and .hfe.  GPL-3.

From LambdaSpeak 4 (lambdaspeak4/tools/mkcpcdisk.py), with the sectors
interleaved here -- see INTERLEAVE.

Nothing on this machine could make a CPC disk, so this does it from scratch:
an AMSDOS DATA-format filesystem, wrapped either in the CPCEMU .dsk container
or MFM-encoded into an HxC .hfe for a hardware floppy emulator.

BASIC programs go on as plain ASCII. AMSDOS decides whether a file is
tokenised or text by looking at the 128-byte header's checksum, and a file
with no valid header is treated as ASCII -- which RUN" handles perfectly
well. That saves tokenising anything.

Usage:  mkcpcdisk.py out_basename FILE.BAS [FILE2.BAS ...]
"""
import sys, os, re, struct

# --- AMSDOS DATA format ---------------------------------------------------
TRACKS, SIDES, SPT, SECSZ = 40, 1, 9, 512
SEC_BASE = 0xC1                 # DATA format sector ids are C1..C9
BLOCK    = 1024                 # two sectors
DIR_BLOCKS = 2                  # 64 entries
FILLER   = 0xE5
TOTAL_BLOCKS = TRACKS * SPT * SECSZ // BLOCK    # 180 of 1 KB, 178 for files
#  The order the sectors pass the head: C1 C6 C2 C7 C3 C8 C4 C9 C5, as
#  AMSDOS's own FORMAT lays them down. In plain C1..C9 order AMSDOS is still
#  busy with one sector when the next goes by, and waits a whole turn of the
#  disc for it: a 16 KB picture took 16 s to load in MAME, not 4.
INTERLEAVE = [0, 5, 1, 6, 2, 7, 3, 8, 4]

def lsec_count():
    return TRACKS * SPT


def ondisk(path):
    """The bytes this file will occupy, after the CRLF fix below.

    It is NOT the size on the host, and the difference is what the disk
    overflowed on: BASIC goes on as ASCII with CRLF line endings, so a
    source file written with bare LF gains one byte per line. PCSPO.BAS at
    5974 bytes and 160 lines is 6134 on the disk, which is 10 bytes inside
    6 blocks. Adding two comment lines to it cost a whole block, and the
    disk had none."""
    raw = open(path, 'rb').read()
    name = os.path.basename(path).upper()
    _, _, ext = name.partition('.')
    if ext.strip() in ('BAS', 'TXT') and b'\x00' not in raw[:128]:
        raw = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        raw = raw.replace(b'\n', b'\r\n')
    return len(raw)


def preflight(files):
    """Refuse an overflowing disk BEFORE writing a byte of it.

    put_block() catches an overflow too, but only when it happens -- and the
    file it is placing then is simply the last one in the list, not the one
    that grew. Reporting that file sent the reader to shed bytes from
    TEST.ROM when PCSPO.BAS was the thing that had changed. So the check
    belongs here, where the whole picture is still in hand."""
    avail = TOTAL_BLOCKS - DIR_BLOCKS
    rows  = [(os.path.basename(f), ondisk(f),
              (ondisk(f) + BLOCK - 1) // BLOCK) for f in files]
    need  = sum(r[2] for r in rows)
    if need <= avail and len(rows) <= 64:
        return
    sys.stderr.write("disk full: %d blocks of files, %d available (%d KB)\n"
                     % (need, avail, avail))
    if len(rows) > 64:
        sys.stderr.write("  and %d files, which is past the 64 directory "
                         "entries a DATA disk has\n" % len(rows))
    sys.stderr.write("  over by %d KB. The biggest, on the disk:\n"
                     % (need - avail))
    for nm, n, b in sorted(rows, key=lambda r: -r[1])[:6]:
        sys.stderr.write("    %-14s %6d bytes  %3d blocks\n" % (nm, n, b))
    sys.stderr.write("  A BASIC file fits in N blocks only up to N*1024 "
                     "bytes INCLUDING its CRLF endings -- that is its host "
                     "size plus one byte a line.\n"
                     "  Either shed bytes from whatever grew, or move a "
                     "payload to a second image.\n")
    raise SystemExit(1)

# --- BASIC 1.0 ---------------------------------------------------------------
#  A 464 ships with BASIC 1.0, and two things that 1.1 takes in its stride
#  break it. Both were on this disc for weeks, unseen, because the 464 they
#  were tried on had a 1.1 ROM switched in.
#
#   * A | in a comment. 1.0 tokenises |NAME even inside REM and ', and RUN
#     then loses whatever follows: the next line vanishes, or the program
#     jumps into nonsense -- PIANO.BAS said "Syntax error in 29296". LIST
#     shows nothing wrong. MAME's cpc464 does exactly the same, which was
#     once written off as an emulation artefact.
#   * A string handed to an RSX directly. 1.0 takes only a variable's
#     address: a$="text":|SAY,@a$.
#
#  And the keywords 1.1 added, which 1.0 reads as variable names.
KW11 = ['FRAME', 'CLEAR INPUT', 'COPYCHR$', 'CURSOR', 'DEC$', 'FILL',
        'GRAPHICS', 'MASK', 'DERR', 'ON BREAK CONT']

def basic_split(line):
    """(code, comment) -- the code with string contents blanked, and the
    REM or ' comment, or ''."""
    out, q = [], False
    for i, c in enumerate(line):
        if q:
            out.append('"' if c == '"' else '_'); q = c != '"'
        elif c == '"':
            out.append(c); q = True
        elif c == "'" or (line[i:i+3].upper() == 'REM' and
                          not re.match(r'[A-Z0-9$]', line[i-1:i].upper())):
            return ''.join(out), line[i:]
        else:
            out.append(c)
    return ''.join(out), ''

def basic10_check(files):
    """Refuse a BASIC program that would not run on BASIC 1.0."""
    bad = []
    for f in files:
        if not f.upper().endswith('.BAS'):
            continue
        for n, line in enumerate(open(f, encoding='latin1').read().splitlines(), 1):
            code, comment = basic_split(line)
            if '|' in comment:
                bad.append((f, n, 'a | in a comment -- 1.0 loses the line after it', line))
            for m in re.finditer(r'\|[A-Za-z][A-Za-z0-9.]*', code):
                rest = line[m.end():].split(':', 1)[0]
                if rest.startswith(',') and any(
                        '"' in a or ('$' in a and not a.strip().startswith('@'))
                        for a in rest[1:].split(',')):
                    bad.append((f, n, 'a string to an RSX -- 1.0 wants a$="...":|X,@a$', line))
            for k in KW11:
                if re.search(r'(?<![A-Z$])' + re.escape(k) + r'(?![A-Z])', code.upper()):
                    bad.append((f, n, k + ' is BASIC 1.1 only', line))
    if not bad:
        return
    for f, n, why, line in bad:
        sys.stderr.write("%s:%d: %s\n    %s\n" % (os.path.basename(f), n, why, line.strip()[:76]))
    sys.stderr.write("%d BASIC 1.0 problem(s); the disc was not written.\n" % len(bad))
    raise SystemExit(1)

def build_filesystem(files):
    """Returns a flat image of TRACKS*SPT sectors, 512 bytes each."""
    basic10_check(files)
    preflight(files)
    data = bytearray([FILLER]) * (lsec_count() * SECSZ)

    def put_block(bno, payload):
        #  A bytearray slice assignment past the end GROWS it, so an
        #  overflowing disk would come out silently oversized and broken
        #  rather than refused. The disk reached 178 of 178 blocks on
        #  2026-11-02, so this is now the live edge rather than a
        #  hypothetical one.
        if bno >= TOTAL_BLOCKS:
            #  preflight() should have refused this already, so reaching here
            #  means the two disagree about what a file costs. Say that,
            #  rather than repeating preflight's advice as though it were a
            #  fresh diagnosis.
            raise SystemExit(
                "internal: %s wanted block %d of %d and preflight() passed "
                "the set -- ondisk() and the writer disagree about size."
                % (cur_name, bno, TOTAL_BLOCKS))
        off = bno * BLOCK
        data[off:off + len(payload)] = payload

    dir_entries = []
    next_block = DIR_BLOCKS
    cur_name = '(none)'
    for path in files:
        cur_name = os.path.basename(path)
        raw = open(path, 'rb').read()
        name = os.path.basename(path).upper()
        stem, _, ext = name.partition('.')
        stem = (stem + ' ' * 8)[:8]
        ext  = (ext  + ' ' * 3)[:3]

        # ASCII BASIC wants CRLF. Locomotive BASIC reads a LOADed .BAS line by
        # line through the same buffer the keyboard uses, and a bare LF is not
        # a line ending to it -- so an LF-only file arrives as one enormous
        # line and the CPC answers "Line too long", with nothing to suggest
        # that the cause is invisible bytes. Normalise here rather than trust
        # every source file to have been written correctly: the ones from an
        # editor were CRLF, the ones written from a shell heredoc were not,
        # and the disk went out silently half broken.
        if ext.strip() in ('BAS', 'TXT') and b'\x00' not in raw[:128]:
            raw = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
            raw = raw.replace(b'\n', b'\r\n')

        # CP/M counts 128-byte records; each extent holds up to 16 blocks.
        blocks = []
        for off in range(0, len(raw), BLOCK):
            chunk = raw[off:off + BLOCK]
            chunk = chunk + bytes([0x1A]) * (BLOCK - len(chunk))  # ^Z pads text
            put_block(next_block, chunk)
            blocks.append(next_block)
            next_block += 1

        records = (len(raw) + 127) // 128
        ext_no = 0
        while blocks or ext_no == 0:
            here, blocks = blocks[:16], blocks[16:]
            rc = min(records, 128)
            records -= rc
            e = bytearray(32)
            e[0] = 0                                   # user 0
            e[1:9]  = stem.encode('ascii')
            e[9:12] = ext.encode('ascii')
            e[12] = ext_no & 0xFF
            e[14] = (ext_no >> 8) & 0xFF
            e[15] = rc
            for i, b in enumerate(here):
                e[16 + i] = b
            dir_entries.append(bytes(e))
            ext_no += 1
            if not blocks:
                break

    if len(dir_entries) > DIR_BLOCKS * BLOCK // 32:
        raise SystemExit("too many directory entries: %d of %d. A file over "
                         "16 KB takes one entry per extent, so this can run "
                         "out before the space does."
                         % (len(dir_entries), DIR_BLOCKS * BLOCK // 32))
    directory = bytearray([FILLER]) * (DIR_BLOCKS * BLOCK)
    for i, e in enumerate(dir_entries):
        directory[i * 32:(i + 1) * 32] = e
    data[0:len(directory)] = directory
    return bytes(data)

def sector(img, track, idx):
    return img[(track * SPT + idx) * SECSZ:][:SECSZ]

# --- the .dsk container ---------------------------------------------------
def write_dsk(img, path):
    tracksz = 0x100 + SPT * SECSZ
    out = bytearray()
    hdr = bytearray(0x100)
    hdr[0:34] = b'MV - CPCEMU Disk-File\r\nDisk-Info\r\n'
    hdr[0x30] = TRACKS
    hdr[0x31] = SIDES
    struct.pack_into('<H', hdr, 0x32, tracksz)
    out += hdr
    for t in range(TRACKS):
        ti = bytearray(0x100)
        ti[0:12] = b'Track-Info\r\n'
        ti[0x10] = t
        ti[0x11] = 0
        ti[0x14] = 2            # 512 bytes
        ti[0x15] = SPT
        ti[0x16] = 0x4E
        ti[0x17] = FILLER
        for p, s in enumerate(INTERLEAVE):
            o = 0x18 + p * 8
            ti[o + 0] = t
            ti[o + 1] = 0
            ti[o + 2] = SEC_BASE + s
            ti[o + 3] = 2
            struct.pack_into('<H', ti, o + 6, SECSZ)
        out += ti
        for s in INTERLEAVE:
            out += sector(img, t, s)
    open(path, 'wb').write(bytes(out))

# --- MFM, and the .hfe container -----------------------------------------
def crc16(data, crc=0xFFFF):
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

class MFM:
    """Builds a track as MFM cells. Each data bit becomes a clock cell and a
    data cell; the clock is 1 only between two zero data bits. Bits go out
    LSB first, which is what HFE stores."""
    def __init__(self):
        self.cells = bytearray()
        self.prev = 0
    def _bit(self, c, d):
        self.cells.append(c)
        self.cells.append(d)
    def byte(self, v):
        for i in range(7, -1, -1):
            d = (v >> i) & 1
            self._bit(1 if (self.prev == 0 and d == 0) else 0, d)
            self.prev = d
    def bytes_(self, bs):
        for b in bs:
            self.byte(b)
    def mark_a1(self):
        """A1 with the clock between bits 4 and 5 suppressed -- the clock
        cell of bit 5, counting from the top, so n == 5 below. It was n == 4
        until 2026-11-06, which made every mark 0x4429: four zero cells in a
        row, which MFM never has and no controller syncs on. The .dsk was
        always right. tools/hfe2dsk.py --check now catches it."""
        v, bits = 0xA1, []
        for i in range(7, -1, -1):
            bits.append((v >> i) & 1)
        for n, d in enumerate(bits):
            c = 1 if (self.prev == 0 and d == 0) else 0
            if n == 5:
                c = 0                      # the missing clock: 0x4489
            self._bit(c, d)
            self.prev = d
    def mark_c2(self):
        v, bits = 0xC2, []
        for i in range(7, -1, -1):
            bits.append((v >> i) & 1)
        for n, d in enumerate(bits):
            c = 1 if (self.prev == 0 and d == 0) else 0
            if n == 4:
                c = 0                      # the missing clock: 0x5224
            self._bit(c, d)
            self.prev = d
    def packed(self, length):
        cells = self.cells[:]
        while len(cells) < length * 8:
            cells.append(0)
        cells = cells[:length * 8]
        out = bytearray()
        for i in range(0, len(cells), 8):
            v = 0
            for j in range(8):
                if cells[i + j]:
                    v |= 1 << j          # LSB first
            out.append(v)
        return bytes(out)

# 250 kbit/s at 300 rpm is 12500 cell bytes a side. Real CPC images carry
# 12508, so match them: a track that is slightly long simply has a little
# more gap at the end, which is what a real one has too.
TRACK_BYTES = 6254

def mfm_track(img, t):
    m = MFM()
    m.bytes_(b'\x4E' * 80)
    m.bytes_(b'\x00' * 12)
    for _ in range(3):
        m.mark_c2()
    m.byte(0xFC)
    m.bytes_(b'\x4E' * 50)
    for s in INTERLEAVE:
        sid = SEC_BASE + s
        m.bytes_(b'\x00' * 12)
        for _ in range(3):
            m.mark_a1()
        m.byte(0xFE)
        idf = bytes([t, 0, sid, 2])
        m.bytes_(idf)
        m.bytes_(struct.pack('>H', crc16(b'\xA1\xA1\xA1\xFE' + idf)))
        m.bytes_(b'\x4E' * 22)
        m.bytes_(b'\x00' * 12)
        for _ in range(3):
            m.mark_a1()
        m.byte(0xFB)
        d = sector(img, t, s)
        m.bytes_(d)
        m.bytes_(struct.pack('>H', crc16(b'\xA1\xA1\xA1\xFB' + d)))
        m.bytes_(b'\x4E' * 82)
    return m.packed(TRACK_BYTES * 2)

def write_hfe(img, path):
    hdr = bytearray(512)
    hdr[0:8] = b'HXCPICFE'
    hdr[8]  = 0                      # revision
    hdr[9]  = TRACKS
    # ONE side, not two. The track data is still stored interleaved in
    # 256-byte chunks with an empty second side -- that is just how HFE lays
    # tracks out -- but this field says how many sides the disk HAS, and a
    # CPC disk has one. Declaring two is what a DDI-3 refuses: images that
    # work on it say 1 here while being byte-for-byte the same size.
    hdr[10] = SIDES
    hdr[11] = 0                      # ISOIBM_MFM
    struct.pack_into('<H', hdr, 12, 250)
    struct.pack_into('<H', hdr, 14, 0)   # RPM: unused, and 0 in real images
    hdr[16] = 0x06                   # CPC_DD_FLOPPYMODE
    hdr[17] = 1
    struct.pack_into('<H', hdr, 18, 1)   # track list at block 1
    hdr[20] = 0xFF                   # write allowed
    hdr[21] = 0xFF                   # single step
    hdr[22] = 0xFF                   # no alternate encoding
    hdr[23] = 0xFF
    hdr[24] = 0xFF
    hdr[25] = 0xFF

    lut = bytearray(512)
    body = bytearray()
    block = 2                        # header + lut
    for t in range(TRACKS):
        side0 = mfm_track(img, t)
        side1 = bytes(len(side0))    # unformatted second side
        inter = bytearray()
        for off in range(0, len(side0), 256):
            inter += side0[off:off + 256].ljust(256, b'\x00')
            inter += side1[off:off + 256].ljust(256, b'\x00')
        pad = (-len(inter)) % 512
        inter += bytes(pad)
        struct.pack_into('<HH', lut, t * 4, block, len(side0) * 2)
        body += inter
        block += len(inter) // 512
    open(path, 'wb').write(bytes(hdr) + bytes(lut) + bytes(body))

if __name__ == '__main__':
    base, files = sys.argv[1], sys.argv[2:]
    image = build_filesystem(files)
    write_dsk(image, base + '.dsk')
    write_hfe(image, base + '.hfe')
    print(f'{base}.dsk  {os.path.getsize(base + ".dsk")} bytes')
    print(f'{base}.hfe  {os.path.getsize(base + ".hfe")} bytes')
