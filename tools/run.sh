#!/bin/bash
# Run a slideshow disc in MAME.  GPL-3.
#
#   tools/run.sh [disc.dsk]             a 6128 with the disc in drive A,
#                                       typing RUN"SLIDES by itself
#   tools/run.sh -m 464 disc.dsk        a 464 with a DDI-1 instead
#   tools/run.sh -n disc.dsk            don't type RUN"SLIDES: just boot
#   tools/run.sh -shots DIR [disc.dsk]  headless: snapshots into DIR, every
#                                       SHOT_EVERY s for RUN_SECONDS s
#
# With no disc: build/slideshow.dsk (or the first of a split set,
# build/slideshow-1.dsk) if there is one, else the demo disc.
# MAME and the CPC ROM sets: $MAME and $CPCROMS, or where they live here.
set -e
here=$(pwd)
self=$(readlink -f "$0")
cd "$(dirname "$self")/.."
MAME=${MAME:-$(command -v mame || echo mame)}
CPCROMS=${CPCROMS:-$HOME/claude/cpc/mame-roms}
machine=cpc6128 shots= auto=1
while [ $# -gt 0 ]; do
  case $1 in
    -m) machine=cpc${2#cpc}; shift 2 ;;
    -n) auto=; shift ;;
    -shots) shots=$2; shift 2 ;;
    -h|--help) sed -n '2,13p' "$self" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option $1 (try -h)" >&2; exit 1 ;;
    *) break ;;
  esac
done
case $machine in
  cpc464) exp="-exp ddi1" ;;
  cpc6128) exp= ;;
  *) echo "-m takes 464 or 6128" >&2; exit 1 ;;
esac

# The disc path is the caller's, relative to where they were, not to here.
if [ -n "$1" ]; then
  case $1 in /*) disc=$1 ;; *) disc=$here/$1 ;; esac
elif [ -f build/slideshow.dsk ]; then
  disc=$PWD/build/slideshow.dsk
elif [ -f build/slideshow-1.dsk ]; then
  disc=$PWD/build/slideshow-1.dsk
else
  disc=$PWD/disk/slideshow.dsk
fi
[ -f "$disc" ] || { echo "no such disc: $disc" >&2; exit 1; }

if [ -z "$shots" ]; then
  cat <<EOF
$machine, drive A: $disc
  Tab           MAME's menu (File Manager -> floppydisk1 changes the disc)
  Insert        the CPC's DEL (backspace)    \`  the CPC's ESC
  Esc           quit MAME (it asks first)
EOF
  boot=(); [ -n "$auto" ] && boot=(-autoboot_delay 3 -autoboot_command 'RUN"SLIDES\n')
  # -ui_active: Tab, Esc and the F keys stay MAME's, everything else types
  # on the CPC; without it the menu needs Scroll Lock first.
  exec "$MAME" $machine $exp -rompath "$CPCROMS" -flop1 "$disc" -skip_gameinfo \
       -window -ui_active -confirm_quit "${boot[@]}"
fi

mkdir -p "$shots"
export FRONT_ADDR=$(awk '$1 == "FRONT" {print $2}' build/view.bin.sym | sed 's/^#//')
export DEADLINE_ADDR=$(awk '$1 == "DEADLINE" {print $2}' build/view.bin.sym | sed 's/^#//')
exec env -u WAYLAND_DISPLAY xvfb-run -a "$MAME" $machine $exp -rompath "$CPCROMS" \
     -flop1 "$disc" -skip_gameinfo -nothrottle -video none -sound none \
     -snapshot_directory "$shots" -autoboot_script emu/show.lua
