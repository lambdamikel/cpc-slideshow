#!/usr/bin/env python3
"""Build slideshow discs for the Amstrad CPC.  GPL-3.

    tools/build.py                        the playlist in slides.txt
    tools/build.py my-playlist.txt        another playlist
    tools/build.py ~/Pictures/show        every picture in a directory, in
                                          file-name order (or that directory's
                                          own slides.txt, if it has one)

    -o build/slideshow   where the discs go (default)
    --secs 10            seconds per picture   } defaults for every picture;
    --mode auto          0, 1, 2 or auto       } a playlist line can still
    --fit                letterbox, don't crop } say otherwise
    --dither fs          fs, bayer or none     }

A DATA disc holds ten pictures. More than that are split over as many discs
as they need -- slideshow-1.dsk, slideshow-2.dsk, ... -- each one complete,
with its own viewer and its share of the playlist. Every disc comes as .dsk
(emulators, real drives) and .hfe (Gotek, HxC). What the CPC will show goes
to build/preview/.

On the CPC:  RUN"SLIDES
"""
import argparse, os, re, shlex, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pic2cpc, mkcpcdisk                                    # noqa: E402

RASM = os.environ.get('RASM') or next(
    (p for p in [os.popen('command -v rasm').read().strip(),
                 os.path.expanduser('~/claude/midi80/toolchain/rasm/rasm')]
     if p and os.access(p, os.X_OK)), 'rasm')

BAS_HEAD = """\
10 ' Picture slideshow -- made by tools/build.py
20 BORDER 0:FOR i=0 TO 15:INK i,0:NEXT:MODE 1
30 MEMORY &3FFF:LOAD"VIEW.BIN",&8000
40 POKE &BE78,&FF:' AMSDOS errors: no messages, no Retry/Ignore/Cancel
50 CALL &8000
60 RESTORE 1000
70 READ f$,s:IF f$="*" THEN 60
80 CALL &8003,@f$,s:GOTO 70
"""


def new_slide(src, defaults, where):
    return {'src': src, 'secs': defaults.secs, 'mode': defaults.mode,
            'opts': list(defaults.opts), 'name': None, 'where': where}


def parse_playlist(path, defaults):
    """Lines of:  picture  [seconds]  [mode]  [option ...]  -- see slides.txt."""
    slides = []
    base = os.path.dirname(os.path.abspath(path))
    for n, line in enumerate(open(path), 1):
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        words = shlex.split(line)
        where = f'{path}:{n}'
        s = new_slide(os.path.join(base, os.path.expanduser(words[0])), defaults, where)
        rest = words[1:]
        if rest and re.fullmatch(r'\d+', rest[0]):
            s['secs'] = int(rest.pop(0))
        if rest and rest[0] in ('0', '1', '2', 'auto'):
            s['mode'] = rest.pop(0)
        for w in rest:
            if w == 'fit':
                s['opts'].append('--fit')
            elif w in ('bayer', 'none', 'fs'):
                s['opts'] += ['--dither', w]
            elif w == 'noblack':
                s['opts'].append('--no-black')
            elif w.startswith('name='):
                s['name'] = w[5:]
            elif '=' in w:
                k, v = w.split('=', 1)
                s['opts'] += ['--' + k, v]
            else:
                sys.exit(f'{where}: unknown option {w!r}')
        if not os.path.isfile(s['src']):
            sys.exit(f'{where}: no such picture {words[0]}')
        slides.append(s)
    return slides


def scan_directory(path, defaults):
    playlist = os.path.join(path, 'slides.txt')
    if os.path.isfile(playlist):
        print(f'using {playlist}')
        return parse_playlist(playlist, defaults)
    return [new_slide(p, defaults, p) for p in pic2cpc.list_pictures(path)]


def assemble(src, out):
    sym = out + '.sym'
    log = subprocess.run([RASM, src, '-ob', out, '-s', '-sa', '-os', sym],
                         capture_output=True, text=True)
    # rasm reports a failed assembly on stdout and still exits 0.
    if log.returncode or re.search(r'error', log.stdout + log.stderr, re.I) or not os.path.exists(out):
        sys.exit(log.stdout + log.stderr)
    return open(out, 'rb').read()


def blocks(nbytes):
    return (nbytes + mkcpcdisk.BLOCK - 1) // mkcpcdisk.BLOCK


def basic(slides):
    lines = [f'{1000 + 10 * i} DATA "{s["disc"]}",{s["secs"]}' for i, s in enumerate(slides)]
    lines.append(f'{1000 + 10 * len(slides)} DATA "*",0')
    return BAS_HEAD + '\n'.join(lines) + '\n'


def split_discs(slides, viewer_bytes):
    """Fill discs in playlist order. A disc's own SLIDES.BAS grows with its
    share of the playlist, so it is costed with the pictures already on it."""
    room = mkcpcdisk.TOTAL_BLOCKS - mkcpcdisk.DIR_BLOCKS
    discs, cur = [], []
    for s in slides:
        trial = cur + [s]
        need = (blocks(len(basic(trial).replace('\n', '\r\n'))) + blocks(viewer_bytes)
                + sum(blocks(128 + 0x4000) for _ in trial))
        if need > room and cur:
            discs.append(cur)
            cur = [s]
        else:
            cur = trial
    discs.append(cur)
    return discs


def main():
    ap = argparse.ArgumentParser(description='Build slideshow discs for the Amstrad CPC.')
    ap.add_argument('source', nargs='?', default=os.path.join(ROOT, 'slides.txt'),
                    help='a playlist, or a directory of pictures')
    ap.add_argument('-o', '--out', default=os.path.join(ROOT, 'build', 'slideshow'))
    ap.add_argument('--secs', type=int, default=10, help='seconds per picture (default 10)')
    ap.add_argument('--mode', default='auto', choices=('0', '1', '2', 'auto'))
    ap.add_argument('--fit', action='store_true', help='letterbox instead of crop')
    ap.add_argument('--dither', default='fs', choices=('fs', 'bayer', 'none'))
    a = ap.parse_args()
    a.opts = (['--fit'] if a.fit else []) + ['--dither', a.dither]

    src = os.path.expanduser(a.source)
    if os.path.isdir(src):
        slides = scan_directory(src, a)
    elif os.path.isfile(src):
        slides = parse_playlist(src, a)
    else:
        sys.exit(f'{a.source}: neither a playlist nor a directory')
    if not slides:
        sys.exit(f'{a.source}: no pictures')
    for s in slides:
        if not 1 <= s['secs'] <= 32767:
            sys.exit(f"{s['where']}: seconds must be 1..32767")

    build = os.path.dirname(os.path.abspath(a.out))
    stem = os.path.basename(a.out)
    work = os.path.join(build, 'disc')
    prev = os.path.join(build, 'preview')
    for d in (work, prev):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    for f in os.listdir(build):                       # discs from a bigger set
        if re.fullmatch(re.escape(stem) + r'(-\d+)?\.(dsk|hfe)', f):
            os.remove(os.path.join(build, f))

    code = assemble(os.path.join(ROOT, 'src', 'view.asm'), os.path.join(build, 'view.bin'))
    viewer = pic2cpc.amsdos_header('VIEW.BIN', 0x8000, len(code), 0x8000) + code

    taken = {'SLIDES', 'VIEW'}
    for s in slides:
        s['disc'] = pic2cpc.disc_name(s['name'] or os.path.splitext(os.path.basename(s['src']))[0],
                              taken) + '.SCR'
        args = pic2cpc.parser().parse_args([s['src'], s['disc'], '--mode', str(s['mode'])]
                                           + s['opts'])
        scr, pal, mode = pic2cpc.convert(s['src'], args.mode, args.fit, args.dither,
                                         args.strength, args.black, args)
        s['bytes'] = pic2cpc.amsdos_header(s['disc'], 0xC000, len(scr)) + scr
        pic2cpc.render_screen(scr).save(os.path.join(prev, s['disc'][:-4].lower() + '.png'))
        print(f"  {s['disc']:13} {s['secs']:5} s  mode {mode}  {os.path.basename(s['src'])}")

    discs = split_discs(slides, len(viewer))
    for n, share in enumerate(discs, 1):
        out = os.path.join(build, stem if len(discs) == 1 else f'{stem}-{n}')
        here = os.path.join(work, os.path.basename(out))
        os.makedirs(here, exist_ok=True)
        files = []
        for name, data in [('SLIDES.BAS', basic(share).encode()), ('VIEW.BIN', viewer)] + \
                          [(s['disc'], s['bytes']) for s in share]:
            path = os.path.join(here, name)
            open(path, 'wb').write(data)
            files.append(path)
        image = mkcpcdisk.build_filesystem(files)
        mkcpcdisk.write_dsk(image, out + '.dsk')
        mkcpcdisk.write_hfe(image, out + '.hfe')
        used = sum(blocks(os.path.getsize(f) + (f.endswith('.BAS') and open(f).read().count('\n')))
                   for f in files)
        print(f'{out}.dsk/.hfe: {len(share)} pictures, {used} of '
              f'{mkcpcdisk.TOTAL_BLOCKS - mkcpcdisk.DIR_BLOCKS} KB')
    print(f'VIEW.BIN {len(code)} bytes.  On the CPC: RUN"SLIDES')


if __name__ == '__main__':
    main()
