#!/usr/bin/env python3
"""Put an AMSDOS header on a raw binary, so LOAD"NAME.BIN" needs no address.

    amsdos.py MPLAY.bin MPLAY.BIN 0x2800 [entry]

AMSDOS decides whether a file has a header by checking the 16-bit sum of the
first 67 bytes against the word at offset 67. A file that fails is treated as
ASCII -- which is exactly how the .BAS files on this disc get on it, and why
a binary needs this.  LambdaSpeak 4, GPL-3.
"""
import sys, struct, os

def header(name, load, length, entry, ftype=2):
    h = bytearray(128)
    stem, ext = (name.split('.') + [''])[:2]
    h[0] = 0                                        # user 0
    h[1:9]   = stem.upper().ljust(8)[:8].encode()
    h[9:12]  = ext.upper().ljust(3)[:3].encode()
    h[18]    = ftype                                # 2 = binary
    struct.pack_into('<H', h, 21, load)
    h[23]    = 0                                    # first (and only) block
    struct.pack_into('<H', h, 24, length)
    struct.pack_into('<H', h, 26, entry)
    struct.pack_into('<I', h, 64, length)           # 24-bit real length
    h[67] = h[68] = 0
    struct.pack_into('<H', h, 67, sum(h[:67]) & 0xFFFF)
    return bytes(h)

def main():
    src, name, load = sys.argv[1], sys.argv[2], int(sys.argv[3], 0)
    body = open(src, 'rb').read()
    entry = int(sys.argv[4], 0) if len(sys.argv) > 4 else load
    out = os.path.join(os.path.dirname(src) or '.', name)
    with open(out, 'wb') as f:
        f.write(header(name, load, len(body), entry))
        f.write(body)
    print(f'{out}  {len(body)} bytes, load {load:#06x}, entry {entry:#06x}')

if __name__ == '__main__':
    main()
