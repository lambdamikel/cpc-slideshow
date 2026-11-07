#!/bin/bash
# Run a slideshow disc in MAME.  GPL-3.
#
#   tools/run.sh                        watch build/slideshow.dsk on a 6128
#   tools/run.sh -m 464 disc.dsk        on a 464 with a DDI-1
#   tools/run.sh -shots DIR [disc.dsk]  headless: snapshots into DIR, every
#                                       SHOT_EVERY s for RUN_SECONDS s
#
# MAME and the CPC ROM sets: $MAME and $CPCROMS, or where they live here.
set -e
cd "$(dirname "$0")/.."
MAME=${MAME:-$(command -v mame || echo mame)}
CPCROMS=${CPCROMS:-$HOME/claude/cpc/mame-roms}
machine=cpc6128 shots=
while [ $# -gt 0 ]; do
  case $1 in
    -m) machine=cpc$2; shift 2 ;;
    -shots) shots=$2; shift 2 ;;
    *) break ;;
  esac
done
disc=${1:-build/slideshow.dsk}
exp=; [ "$machine" = cpc464 ] && exp="-exp ddi1"

if [ -z "$shots" ]; then
  exec "$MAME" $machine $exp -rompath "$CPCROMS" -flop1 "$disc" -skip_gameinfo -window \
       -autoboot_delay 3 -autoboot_command 'RUN"SLIDES\n'
fi

mkdir -p "$shots"
export FRONT_ADDR=$(awk '$1 == "FRONT" {print $2}' build/view.bin.sym | sed 's/^#//')
export DEADLINE_ADDR=$(awk '$1 == "DEADLINE" {print $2}' build/view.bin.sym | sed 's/^#//')
exec env -u WAYLAND_DISPLAY xvfb-run -a "$MAME" $machine $exp -rompath "$CPCROMS" \
     -flop1 "$disc" -skip_gameinfo -nothrottle -video none -sound none \
     -snapshot_directory "$shots" -autoboot_script emu/show.lua
