; VIEW.BIN -- picture slideshow viewer for the Amstrad CPC.  GPL-3.
;
; Loaded at &8000 by SLIDES.BAS, which keeps the playlist:
;
;     CALL &8000               init: all inks black, first picture at once
;     CALL &8003,@f$,secs      show f$ after the current picture has had
;                              its time, and give f$ secs seconds
;     PEEK(&8006)              0 if the last CALL &8003 showed its picture,
;                              1 if the file could not be loaded
;
; The picture is loaded into the hidden one of two screens, &4000 and
; &C000, while the current one stays up, so the disc time is spent inside
; the display time. When that runs out, or a key is pressed, the current
; picture fades out through the half-intensity colours to black, the CRTC
; is pointed at the other screen, the mode is set, and the new picture
; fades in. Each .SCR carries its mode and palette at offset &07D0 (see
; tools/pic2cpc.py), in the 48 bytes after the first 25 character rows
; that are never displayed.
;
; Needs MEMORY &3FFF first, so BASIC keeps out of &4000-&7FFF. Runs on
; BASIC 1.0 and 1.1: everything goes through the firmware jumpblock.

KM_READ_CHAR    equ #BB09
TXT_VDU_ENABLE  equ #BB54
TXT_VDU_DISABLE equ #BB57
SCR_SET_BASE    equ #BC08
SCR_SET_INK     equ #BC32
SCR_SET_BORDER  equ #BC38
CAS_IN_OPEN     equ #BC77
CAS_IN_CLOSE    equ #BC7A
CAS_IN_ABANDON  equ #BC7D
CAS_IN_DIRECT   equ #BC83
KL_TIME_PLEASE  equ #BD0D
MC_SET_MODE     equ #BD1C

META_HI         equ #07             ; &07D0: mode, border, 16 pens. Two
META_LO         equ #D0             ; halves, not META/256: rasm divides in
                                    ; floating point and rounds 7.8 up to 8
FADE_HALTS      equ 30              ; 300 Hz ticks per fade step: 0.1 s
IOBUF           equ #9000           ; 2 KB for CAS IN OPEN, up to &97FF
; AMSDOS runs with interrupts off while it waits for and reads a sector, and
; the firmware's 300 Hz clock stops with them: loading a picture costs the
; clock 1.5 to 2.6 s of the ~4.3 it takes (measured in MAME, more the further
; in the file is). Credit the average back, or every picture overstays by it.
LOAD_LOST       equ 600             ; 2.0 s

                org #8000
                jp init             ; &8000
                jp show             ; &8003
status          db 0                ; &8006

; --- CALL &8000 ------------------------------------------------------------
init:
                ld a,#C0            ; BASIC's own screen
                ld (front),a
                call SCR_SET_BASE
                ld hl,black
                ld de,curpal
                ld bc,17
                ldir
                ld hl,curpal
                xor a
                call show_pal
                call KL_TIME_PLEASE
                ld (deadline),hl
                ld (deadline+2),de
                xor a
                ld (status),a
                ret

; --- CALL &8003,@f$,secs -----------------------------------------------------
show:
                cp 2
                ret nz              ; wrong number of parameters: ignore
                ld a,1
                ld (status),a
                ld l,(ix+0)
                ld h,(ix+1)
                ld (secs),hl
                ld l,(ix+2)         ; HL -> f$'s descriptor: length, address
                ld h,(ix+3)
                ld a,(hl)
                or a
                ret z
                cp 13               ; NAME.EXT is 12 at most
                jr c,.lenok
                ld a,12
.lenok          ld c,a
                ld b,0
                inc hl
                ld e,(hl)
                inc hl
                ld d,(hl)
                ex de,hl            ; HL -> the text
                ld de,fname         ; copied up here, out from under the
                push bc             ; lower ROM the firmware may page in
                ldir
                pop bc
                ld b,c
                ; AMSDOS says "NAME.SCR not found" even with &BE78 set, and
                ; would say it across the picture on show. Text off while
                ; loading; back on after, so a Break is still seen.
                push bc
                call TXT_VDU_DISABLE
                pop bc
                call load
                push af
                call TXT_VDU_ENABLE
                pop af
                ret nc              ; not loaded: status stays 1
                ld hl,(deadline)    ; deadline -= LOAD_LOST
                ld bc,LOAD_LOST
                or a
                sbc hl,bc
                ld (deadline),hl
                jr nc,.wait
                ld hl,(deadline+2)
                dec hl
                ld (deadline+2),hl

; Wait out the current picture. A key cuts it short.
.wait           call KM_READ_CHAR
                jr c,.go
                call KL_TIME_PLEASE ; DEHL = now, in 1/300 s
                ld bc,(deadline)
                or a
                sbc hl,bc
                ex de,hl
                ld bc,(deadline+2)
                sbc hl,bc           ; HL = high word of now - deadline
                jp p,.go            ; reached
                halt
                jr .wait

.go             ld hl,curpal        ; fade out
                ld a,1
                call show_pal
                call pause
                ld hl,curpal
                xor a
                call show_pal
                call pause

                ld a,(front)        ; flip, in the dark
                xor #80
                ld (front),a
                call SCR_SET_BASE
                ld a,(front)
                add a,META_HI
                ld h,a
                ld l,META_LO        ; HL -> the new picture's metadata
                ld a,(hl)
                and 3
                cp 3
                jr c,.modeok
                xor a
.modeok         push hl
                call MC_SET_MODE
                pop hl
                inc hl
                ld de,curpal        ; border, pens 0..15
                ld bc,17
                ldir
                call pause          ; the CRTC takes the new base at the
                                    ; next frame: let it, still black
                ld hl,curpal        ; fade in
                ld a,1
                call show_pal
                call pause
                ld hl,curpal
                ld a,2
                call show_pal

                call KL_TIME_PLEASE ; deadline = now + 300 * secs
                ld bc,(secs)
.add            ld a,b
                or c
                jr z,.added
                push bc
                ld bc,300
                add hl,bc
                jr nc,.nc
                inc de
.nc             pop bc
                dec bc
                jr .add
.added          ld (deadline),hl
                ld (deadline+2),de
                xor a
                ld (status),a
                ret

; --- the disc ---------------------------------------------------------------
; B = length of the name at fname. Loads it into the hidden screen.
; Carry set if it loaded.
load:
                ld hl,fname
                ld de,IOBUF
                call CAS_IN_OPEN
                ret nc
                ld a,(front)
                xor #80             ; &C0 <-> &40: the hidden screen
                ld h,a
                ld l,0
                call CAS_IN_DIRECT
                jr nc,.fail
                jp CAS_IN_CLOSE     ; carry set on a good close
.fail           call CAS_IN_ABANDON
                or a
                ret

; --- the palette ------------------------------------------------------------
; HL -> border, pens 0..15, as firmware colours. A = 2 full, 1 half, 0 black.
; The firmware puts the inks out at the next frame flyback.
show_pal:
                ld (level),a
                ld (palptr),hl
                ld a,(hl)
                call dim
                ld b,a
                ld c,a
                call SCR_SET_BORDER
                xor a
.pen            ld (pen),a
                ld hl,(palptr)
                inc hl
                ld (palptr),hl
                ld a,(hl)
                call dim
                ld b,a
                ld c,a
                ld a,(pen)
                call SCR_SET_INK
                ld a,(pen)
                inc a
                cp 16
                jr nz,.pen
                ret

; A = firmware colour -> A dimmed to (level). Corrupts DE, HL.
dim:
                cp 27
                jr c,.inrange
                xor a
.inrange        ld e,a
                ld a,(level)
                cp 2
                ld a,e
                ret z
                ld a,(level)
                or a
                ret z               ; black
                ld d,0
                ld hl,half
                add hl,de
                ld a,(hl)
                ret

pause:
                ld b,FADE_HALTS
.h              halt
                djnz .h
                ret

; Each gun one level down: full -> half -> off.
half            db 0,0,1,0,0,1,3,3,4,0,0,1,0,0,1,3,3,4,9,9,10,9,9,10,12,12,13

; --- state --------------------------------------------------------------------
front           db #C0
level           db 0
pen             db 0
palptr          dw 0
secs            dw 0
deadline        dw 0,0
fname           defs 12,0
curpal          defs 17,0
black           defs 17,0

                assert $ <= IOBUF
