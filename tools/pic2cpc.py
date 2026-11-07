#!/usr/bin/env python3
"""Convert pictures into Amstrad CPC screen files.  GPL-3.

    pic2cpc.py IN.jpg OUT.SCR [--mode 0|1|2|auto] [--fit] [--dither fs|bayer|none]
                              [--preview OUT.png] [--saturation 1.2] ...
    pic2cpc.py IN_DIR OUT_DIR [same options]

Given a directory, every picture in it is converted, in file-name order, to
OUT_DIR/NAME.SCR (an 8.3 name made from the file name), each with a preview
OUT_DIR/name.png of what the CPC will show.

The output is a 16 KB screen dump with an AMSDOS header (binary, load &C000),
so LOAD"OUT.SCR",&C000 shows it from BASIC as well as from VIEW.BIN.

The picture carries its own mode and palette, in the 48 unused bytes after
the first 2000 of the screen (offset &07D0 -- never displayed):

    &07D0  mode, 0..2
    &07D1  border, firmware colour 0..26
    &07D2  pens 0..15, firmware colours 0..26

Firmware colour n is 9*G + 3*R + B with each gun at level 0, 1 or 2 -- the
numbers INK takes.  Pen 0 is always black unless --no-black: the viewer fades
through it, and --fit letterboxes in it.

Geometry.  The 320x200 (mode 1) picture area is about 1.5 times as wide as it
is tall on a real monitor and in MAME, so a mode 1 pixel is very nearly
square, a mode 0 pixel twice as wide as tall and a mode 2 pixel half as wide.
Pictures are cropped (default) or letterboxed (--fit) to 1.5:1 and then
resampled to the mode's grid.  A source that is already CPC pixels -- W x 200,
or W x 400 line-doubled, with W the mode's width -- is taken pixel for pixel.
"""
import argparse, os, re, struct, sys
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

LEVELS = (0x00, 0x80, 0xFF)          # gun off, half, full
AREA_ASPECT = 1.5                    # width / height of the 320x200 area
MODES = {0: (160, 16), 1: (320, 4), 2: (640, 2)}
META = 0x07D0


def fw_rgb(n):
    g, r, b = n // 9, (n % 9) // 3, n % 3
    return LEVELS[r], LEVELS[g], LEVELS[b]

CPC_RGB = np.array([fw_rgb(n) for n in range(27)], dtype=np.float32)
#  Perceptual-ish weights for squared RGB distance: green counts most.
WEIGHT = np.array([2.0, 4.0, 3.0], dtype=np.float32)
LUMA = np.array([0.30, 0.59, 0.11], dtype=np.float32)


def dist2(pix, pal):
    """Weighted squared distance, pixels (N,3) against colours (K,3) -> (N,K)."""
    d = pix[:, None, :] - pal[None, :, :]
    return (d * d * WEIGHT).sum(axis=2)


# --- geometry -----------------------------------------------------------------

def is_cpc_pixels(im, width):
    return im.size in ((width, 200), (width, 400))


def auto_mode(im):
    """A picture that is already CPC pixels keeps its mode; one with two
    colours is line art or text and gets mode 2's resolution, four colours
    mode 1; anything else is a photo, mode 0."""
    for mode, (width, _) in MODES.items():
        if is_cpc_pixels(im, width):
            return mode
    small = im.convert('RGB')
    small.thumbnail((640, 400))
    colours = small.getcolors(5)
    if colours is not None and len(colours) <= 2:
        return 2
    if colours is not None and len(colours) <= 4:
        return 1
    return 0


def prepare(im, mode, fit, args):
    """Crop or letterbox to the CPC picture area, resample to the mode's grid.
    Returns (float RGB array HxWx3, mask of letterbox pixels or None)."""
    width = MODES[mode][0]
    im = ImageOps.exif_transpose(im).convert('RGB')
    if is_cpc_pixels(im, width):
        im = im.resize((width, 200), Image.NEAREST)
        return np.asarray(im, dtype=np.float32), None, True

    if args.saturation != 1.0:
        im = ImageEnhance.Color(im).enhance(args.saturation)
    if args.contrast != 1.0:
        im = ImageEnhance.Contrast(im).enhance(args.contrast)
    if args.autocontrast:
        im = ImageOps.autocontrast(im, cutoff=args.autocontrast)

    w, h = im.size
    mask = None
    if not fit:                                   # crop to fill
        if w / h > AREA_ASPECT:
            nw = round(h * AREA_ASPECT)
            x0 = round((w - nw) * args.xalign)
            im = im.crop((x0, 0, x0 + nw, h))
        else:
            nh = round(w / AREA_ASPECT)
            y0 = round((h - nh) * args.yalign)
            im = im.crop((0, y0, w, y0 + nh))
        im = im.resize((width, 200), Image.LANCZOS)
    else:                                         # letterbox in black
        # Size the picture in "area units": the area is AREA_ASPECT x 1.
        if w / h > AREA_ASPECT:
            fw, fh = 1.0, (h / w) * AREA_ASPECT
        else:
            fw, fh = (w / h) / AREA_ASPECT, 1.0
        pw, ph = max(1, round(width * fw)), max(1, round(200 * fh))
        pic = im.resize((pw, ph), Image.LANCZOS)
        canvas = Image.new('RGB', (width, 200))
        ox, oy = (width - pw) // 2, (200 - ph) // 2
        canvas.paste(pic, (ox, oy))
        im = canvas
        mask = np.ones((200, width), dtype=bool)
        mask[oy:oy + ph, ox:ox + pw] = False
    if args.sharpen:
        from PIL import ImageFilter
        im = im.filter(ImageFilter.UnsharpMask(radius=1, percent=int(args.sharpen * 100), threshold=2))
    return np.asarray(im, dtype=np.float32), mask, False


# --- palette ------------------------------------------------------------------

def choose_palette(img, k, force_black, mask=None):
    """Pick k of the 27 colours minimising the summed distance of every
    picture pixel to its nearest chosen colour: greedy, then swaps until no
    single swap helps.  Returns firmware colour numbers, black first."""
    pix = img.reshape(-1, 3)
    if mask is not None:
        pix = pix[~mask.reshape(-1)]
    if len(pix) > 40000:                      # mode 2: subsample, plenty
        pix = pix[np.random.default_rng(1).choice(len(pix), 40000, replace=False)]
    D = dist2(pix, CPC_RGB)                   # (N, 27)

    def cost(sel):
        return D[:, sel].min(axis=1).sum()

    chosen = [0] if force_black else []
    while len(chosen) < k:
        best = min((c for c in range(27) if c not in chosen),
                   key=lambda c: cost(chosen + [c]))
        chosen.append(best)
    fixed = 1 if force_black else 0
    improved = True
    while improved:
        improved = False
        base = cost(chosen)
        for i in range(fixed, k):
            for c in range(27):
                if c in chosen:
                    continue
                trial = chosen[:i] + [c] + chosen[i + 1:]
                t = cost(trial)
                if t < base - 1e-3:
                    chosen, base, improved = trial, t, True
    head, rest = chosen[:fixed], chosen[fixed:]
    rest.sort(key=lambda n: float(CPC_RGB[n] @ LUMA))      # dark to light
    return head + rest


# --- dithering ----------------------------------------------------------------

BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6],
                    [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.float32) + 0.5) / 16 - 0.5


def dither(img, palette, method, strength, mode, mask=None):
    """Map the picture onto palette indices (H x W uint8)."""
    pal = CPC_RGB[palette]
    h, w, _ = img.shape
    if method == 'none':
        idx = dist2(img.reshape(-1, 3), pal).argmin(axis=1).reshape(h, w)
    elif method == 'bayer':
        # Mode 0 pixels are wide: tile the matrix 1 pixel per column there
        # and it still reads as a fine pattern.
        t = np.tile(BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w]
        spread = 128.0 * strength
        idx = dist2((img + t[..., None] * spread).reshape(-1, 3), pal) \
            .argmin(axis=1).reshape(h, w)
    else:                                      # Floyd-Steinberg, serpentine
        work = img.copy()
        idx = np.zeros((h, w), dtype=np.uint8)
        for y in range(h):
            xs = range(w) if y % 2 == 0 else range(w - 1, -1, -1)
            step = 1 if y % 2 == 0 else -1
            row, nxt = work[y], work[y + 1] if y + 1 < h else None
            for x in xs:
                old = np.clip(row[x], 0, 255)
                d = pal - old
                i = int(((d * d) * WEIGHT).sum(axis=1).argmin())
                idx[y, x] = i
                err = (old - pal[i]) * strength
                xn = x + step
                if 0 <= xn < w:
                    row[xn] += err * (7 / 16)
                if nxt is not None:
                    xp = x - step
                    if 0 <= xp < w:
                        nxt[xp] += err * (3 / 16)
                    nxt[x] += err * (5 / 16)
                    if 0 <= xn < w:
                        nxt[xn] += err * (1 / 16)
    if mask is not None:
        idx[mask] = 0                           # letterbox stays pen 0, black
    return idx.astype(np.uint8)


# --- screen encoding ----------------------------------------------------------

def encode_byte(mode, px):
    if mode == 0:
        a, b = px
        return (((a & 1) << 7) | ((b & 1) << 6) | ((a >> 2 & 1) << 5) | ((b >> 2 & 1) << 4)
                | ((a >> 1 & 1) << 3) | ((b >> 1 & 1) << 2) | ((a >> 3 & 1) << 1) | (b >> 3 & 1))
    if mode == 1:
        v = 0
        for n, p in enumerate(px):
            v |= ((p & 1) << (7 - n)) | ((p >> 1 & 1) << (3 - n))
        return v
    v = 0
    for n, p in enumerate(px):
        v |= (p & 1) << (7 - n)
    return v


def decode_byte(mode, v):
    if mode == 0:
        a = (v >> 7 & 1) | (v >> 3 & 1) << 1 | (v >> 5 & 1) << 2 | (v >> 1 & 1) << 3
        b = (v >> 6 & 1) | (v >> 2 & 1) << 1 | (v >> 4 & 1) << 2 | (v & 1) << 3
        return [a, b]
    if mode == 1:
        return [(v >> (7 - n) & 1) | (v >> (3 - n) & 1) << 1 for n in range(4)]
    return [v >> (7 - n) & 1 for n in range(8)]


def addr(y, xbyte):
    return (y % 8) * 0x800 + (y // 8) * 80 + xbyte


def encode_screen(idx, mode, palette, border=0):
    per = {0: 2, 1: 4, 2: 8}[mode]
    scr = bytearray(0x4000)
    for y in range(200):
        row = idx[y]
        for xb in range(80):
            scr[addr(y, xb)] = encode_byte(mode, [int(p) for p in row[xb * per:(xb + 1) * per]])
    pens = list(palette) + [0] * (16 - len(palette))
    scr[META] = mode
    scr[META + 1] = border
    scr[META + 2:META + 18] = bytes(pens)
    return bytes(scr)


def render_screen(scr):
    """A 16 KB screen with its metadata -> PIL image, 640x400, as a monitor
    shows the picture area."""
    mode, pens = scr[META] & 3, list(scr[META + 2:META + 18])
    if mode == 3:
        mode = 0
    per = {0: 2, 1: 4, 2: 8}[mode]
    width = MODES[mode][0]
    out = np.zeros((200, width, 3), dtype=np.uint8)
    for y in range(200):
        for xb in range(80):
            for n, p in enumerate(decode_byte(mode, scr[addr(y, xb)])):
                c = pens[p] if pens[p] < 27 else 0
                out[y, xb * per + n] = CPC_RGB[c]
    return Image.fromarray(out).resize((640, 400), Image.NEAREST)


# --- AMSDOS -------------------------------------------------------------------

def amsdos_header(name, load, length, entry=0, ftype=2):
    h = bytearray(128)
    stem, ext = (name.upper().split('.') + [''])[:2]
    h[1:9] = stem.ljust(8)[:8].encode()
    h[9:12] = ext.ljust(3)[:3].encode()
    h[18] = ftype
    struct.pack_into('<H', h, 21, load)
    struct.pack_into('<H', h, 24, length)
    struct.pack_into('<H', h, 26, entry)
    struct.pack_into('<I', h, 64, length)
    struct.pack_into('<H', h, 67, sum(h[:67]) & 0xFFFF)
    return bytes(h)


# --- directories ----------------------------------------------------------------

PICTURES = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff')


def natural(name):
    """Sort key putting 2.jpg before 10.jpg."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', name)]


def list_pictures(path):
    """The pictures in a directory, in file-name order; unreadable ones are
    reported and left out."""
    out = []
    for name in sorted(os.listdir(path), key=natural):
        full = os.path.join(path, name)
        if name.startswith('.') or not name.lower().endswith(PICTURES) or not os.path.isfile(full):
            continue
        try:
            Image.open(full).verify()
        except Exception as e:
            print(f'  skipping {name}: {e}')
            continue
        out.append(full)
    return out


def disc_name(stem, taken):
    """An AMSDOS name, at most 8 of A-Z 0-9, not in taken (which it joins)."""
    base = re.sub(r'[^A-Z0-9]', '', stem.upper())[:8] or 'PIC'
    name, i = base, 1
    while name in taken:
        suffix = str(i)
        name, i = base[:8 - len(suffix)] + suffix, i + 1
    taken.add(name)
    return name


# --- command line ---------------------------------------------------------------

def convert(src, mode=0, fit=False, dither_method='fs', strength=0.85,
            force_black=True, args=None):
    if args is None:
        args = parser().parse_args([src, 'x'])
    im = Image.open(src)
    if mode == 'auto':
        mode = auto_mode(im)
    mode = int(mode)
    img, mask, exact = prepare(im, mode, fit, args)
    k = MODES[mode][1]
    if exact:
        dither_method = 'none'
    palette = choose_palette(img, k, force_black, mask)
    idx = dither(img, palette, dither_method, strength, mode, mask)
    return encode_screen(idx, mode, palette), palette, mode


def parser():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('src')
    p.add_argument('out')
    p.add_argument('--mode', default='auto', choices=('0', '1', '2', 'auto'),
                   help='auto: CPC-sized or 2/4-colour pictures get mode 2/1, photos mode 0')
    p.add_argument('--fit', action='store_true', help='letterbox instead of crop')
    p.add_argument('--dither', default='fs', choices=('fs', 'bayer', 'none'))
    p.add_argument('--strength', type=float, default=0.85, help='dither strength, 0..1')
    p.add_argument('--saturation', type=float, default=1.15)
    p.add_argument('--contrast', type=float, default=1.0)
    p.add_argument('--autocontrast', type=float, default=0.5,
                   help='percent clipped at each end, 0 = off')
    p.add_argument('--sharpen', type=float, default=0.6, help='unsharp mask amount, 0 = off')
    p.add_argument('--xalign', type=float, default=0.5, help='crop position, 0 left .. 1 right')
    p.add_argument('--yalign', type=float, default=0.5, help='crop position, 0 top .. 1 bottom')
    p.add_argument('--no-black', dest='black', action='store_false')
    p.add_argument('--preview', help='also write what the CPC will show, as PNG')
    return p


def convert_file(src, out, args, preview=None):
    scr, palette, mode = convert(src, args.mode, args.fit, args.dither, args.strength,
                                 args.black, args)
    with open(out, 'wb') as f:
        f.write(amsdos_header(os.path.basename(out), 0xC000, len(scr)))
        f.write(scr)
    if preview:
        render_screen(scr).save(preview)
    print(f'{out}: mode {mode}, inks {",".join(map(str, palette))}  <- {os.path.basename(src)}')


def main():
    args = parser().parse_args()
    if not os.path.isdir(args.src):
        convert_file(args.src, args.out, args, args.preview)
        return
    pictures = list_pictures(args.src)
    if not pictures:
        sys.exit(f'{args.src}: no pictures')
    os.makedirs(args.out, exist_ok=True)
    taken = set()
    for src in pictures:
        name = disc_name(os.path.splitext(os.path.basename(src))[0], taken)
        convert_file(src, os.path.join(args.out, name + '.SCR'), args,
                     os.path.join(args.out, name.lower() + '.png'))
    print(f'{len(pictures)} pictures -> {args.out}')


if __name__ == '__main__':
    main()
