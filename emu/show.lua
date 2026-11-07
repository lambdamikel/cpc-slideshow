-- Boot, RUN"SLIDES, and watch the slideshow. Every SHOT_EVERY seconds of
-- emulated time (0 = never) MAME saves a snapshot -- what the emulated
-- monitor shows: palette, mode and all -- and every flip of the screen is
-- logged to the frame, with the viewer's own clock beside it.
-- SHOT_AFTER_FLIP=3 also takes one snapshot of each picture, 3 s after it
-- went up: one per slide, for the README.
local NK = manager.machine.natkeyboard; NK.in_use = true
local PROG = manager.machine.devices[":maincpu"].spaces["program"]
local every = tonumber(os.getenv("SHOT_EVERY") or "2") * 50
local stop  = tonumber(os.getenv("RUN_SECONDS") or "60") * 50
local front = tonumber(os.getenv("FRONT_ADDR") or "0", 16)
local dl    = tonumber(os.getenv("DEADLINE_ADDR") or "0", 16)
local typed = os.getenv("TYPE") or 'RUN"SLIDES{ENTER}'
local after = tonumber(os.getenv("SHOT_AFTER_FLIP") or "0") * 50
local F, n, last, lastF, flips = 0, 0, nil, nil, 0
local function u32(a)
  return PROG:read_u8(a) + 256 * (PROG:read_u8(a + 1) + 256 * (PROG:read_u8(a + 2) + 256 * PROG:read_u8(a + 3)))
end
-- keep the subscription: MAME drops the notifier when it is collected
SUB = emu.add_machine_frame_notifier(function()
  F = F + 1
  if F == 150 then NK:post_coded(typed) end
  local f = PROG:read_u8(front)
  if F > 150 and f ~= last then
    print(string.format("flip  t=%6.2fs  front=%02X  shown for %5.2fs  deadline=%d",
      F / 50, f, lastF and (F - lastF) / 50 or 0, u32(dl)))
    last, lastF, flips = f, F, flips + 1
  end
  -- flips 1 and 2 are the first look and CALL &8000; pictures start at 3
  if after > 0 and flips >= 3 and F == lastF + after then
    manager.machine.video:snapshot()
    print(string.format("slide shot t=%6.2fs", F / 50))
  end
  if every > 0 and F > 150 and F % every == 0 then
    manager.machine.video:snapshot()
    n = n + 1
  end
  if F >= stop then manager.machine:exit() end
end)
