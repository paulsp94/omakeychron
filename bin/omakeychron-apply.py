#!/usr/bin/python3 -I
"""Set a Keychron (QMK/VIA firmware) keyboard's RGB matrix to the Omarchy theme color.

Reads the active theme's keyboard.rgb, boosts saturation/value so the color reads
true on LEDs instead of washed-out, and writes it to the first responding Keychron
VIA raw-HID interface (usage page 0xFF60). Wired only: the Keychron Link dongle
exposes a VIA interface but never answers over 2.4GHz, so every candidate must
pass a protocol-version handshake before use. The color is saved to the keyboard's
onboard memory, so it survives replugs and works while wireless afterwards.

No arguments, no network, no files written. Exit 0 when there is nothing to do
(no keyboard.rgb in the theme), non-zero when a keyboard or the theme file was
found but could not be used.
"""
import colorsys
import glob
import os
import pwd
import re
import select
import stat
import sys
import time

VENDOR_ID = "3434"  # Keychron
MAX_RGB_BYTES = 64  # keyboard.rgb holds one hex color; anything bigger is not ours to parse
DEADLINE = time.monotonic() + 10.0  # absolute budget for the whole run

CMD_CUSTOM_SET = 0x07
CMD_CUSTOM_SAVE = 0x09
CHANNEL_RGB_MATRIX = 0x03
VALUE_EFFECT = 0x02
VALUE_COLOR = 0x04
EFFECT_SOLID_COLOR = 0x01
VIA_CUSTOM_CHANNELS = 0x000B  # protocol version that introduced channel-based values
LEGACY_VALUE_EFFECT = 0x81  # VIA v10 id_qmk_rgblight_effect
LEGACY_VALUE_COLOR = 0x83  # VIA v10 id_qmk_rgblight_color
VIA_UNHANDLED = 0xFF  # v11+ replaces byte 0 with this when it refuses a command


def read_theme_color():
    """Bounded, descriptor-validated read of the theme's keyboard.rgb.

    Returns a lowercase RRGGBB string, or None when the theme has no keyboard.rgb
    (exit-0 case). Any other rejected state raises.
    """
    # passwd, not $HOME: the environment is inherited and rewritable by any
    # same-UID process; the passwd entry is the trust anchor.
    home = pwd.getpwuid(os.geteuid()).pw_dir
    # current/theme is a symlink by design (that is how omarchy-theme-set switches
    # themes), so the parents resolve normally; O_NOFOLLOW guards the final
    # component and O_NONBLOCK keeps a planted FIFO from hanging the open.
    path = os.path.join(home, ".local/state/omarchy/current/theme/keyboard.rgb")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_size > MAX_RGB_BYTES:
            raise PermissionError("refusing keyboard.rgb (not a small regular file owned by us)")
        os.set_blocking(fd, True)
        data = b""
        while len(data) <= MAX_RGB_BYTES:
            chunk = os.read(fd, MAX_RGB_BYTES + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > MAX_RGB_BYTES:
            raise PermissionError("keyboard.rgb grew past the limit")
    finally:
        os.close(fd)
    text = data.decode("ascii", "strict").strip().lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", text):
        raise ValueError("keyboard.rgb does not hold one RRGGBB color")
    return text.lower()


def vivid(hexcolor):
    """Boost saturation and value so theme palette colors read true on LEDs.

    Theme colors are often desaturated, which renders washed-out and blue-shifted
    on RGB hardware (jade -> turquoise). Near-grays pass through untouched (their
    hue is meaningless). LEDs render the green-cyan band bluer than screens do,
    so hues between 120 and 180 degrees are pulled most of the way back to green.
    """
    r, g, b = (int(hexcolor[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s >= 0.15:
        s = max(s, 0.9)
        v = 1.0
        deg = h * 360
        if 120 < deg < 180:
            h = (120 + (deg - 120) * 0.2) / 360
    return "%02x%02x%02x" % tuple(int(x * 255) for x in colorsys.hsv_to_rgb(h, s, v))


def via_hidraw_candidates():
    """Yield /dev/hidraw nodes whose descriptor declares the VIA usage page.

    The sysfs metadata is validated on the descriptor as root-owned regular
    files before being believed; the device node itself is checked to be a
    root-owned character device after open.
    """
    for uevent in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            info = _read_sysfs(uevent)
        except OSError:
            continue
        if f"HID_ID=0003:0000{VENDOR_ID}:".encode() not in info:
            continue
        desc_path = os.path.join(os.path.dirname(uevent), "report_descriptor")
        try:
            desc = _read_sysfs(desc_path)
        except OSError:
            continue
        if desc.startswith(b"\x06\x60\xff\x09\x61"):  # usage page 0xFF60, usage 0x61
            yield "/dev/" + uevent.split("/")[4]


def _read_sysfs(path, cap=4096):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != 0:
            raise OSError("not a root-owned sysfs file")
        return os.read(fd, cap)
    finally:
        os.close(fd)


def xfer(fd, payload):
    remaining = DEADLINE - time.monotonic()
    if remaining <= 0:
        sys.exit("deadline exceeded talking to the keyboard")
    os.write(fd, b"\x00" + bytes(payload) + bytes(32 - len(payload)))
    ready, _, _ = select.select([fd], [], [], min(1.5, remaining))
    return os.read(fd, 32) if ready else None


def apply_color(hexcolor):
    r, g, b = (int(hexcolor[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    for dev in via_hidraw_candidates():
        try:
            fd = os.open(dev, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            continue  # no udev rule / no access to this node; try the next one
        try:
            if not stat.S_ISCHR(os.fstat(fd).st_mode):
                continue
            version = xfer(fd, [0x01])  # id_get_protocol_version handshake
            if version is None:
                continue  # the Link dongle's dead interface lands here
            if (version[1] << 8 | version[2]) >= VIA_CUSTOM_CHANNELS:
                ok = xfer(fd, [CMD_CUSTOM_SET, CHANNEL_RGB_MATRIX, VALUE_EFFECT, EFFECT_SOLID_COLOR])
                ok2 = xfer(fd, [CMD_CUSTOM_SET, CHANNEL_RGB_MATRIX, VALUE_COLOR, int(h * 255), int(s * 255)])
                xfer(fd, [CMD_CUSTOM_SAVE, CHANNEL_RGB_MATRIX])
            else:
                # VIA v10 and older (e.g. Q1 Pro 1.02): no channel byte. The values
                # live at fixed IDs 0x80-0x83, named rgblight but routed to the RGB
                # matrix on these boards. v10 never signals a refused command.
                ok = xfer(fd, [CMD_CUSTOM_SET, LEGACY_VALUE_EFFECT, EFFECT_SOLID_COLOR])
                ok2 = xfer(fd, [CMD_CUSTOM_SET, LEGACY_VALUE_COLOR, int(h * 255), int(s * 255)])
                xfer(fd, [CMD_CUSTOM_SAVE])
            if ok is None or ok2 is None:
                sys.exit(f"{dev}: keyboard stopped responding mid-write")
            if VIA_UNHANDLED in (ok[0], ok2[0]):
                sys.exit(f"{dev}: keyboard refused the lighting commands (VIA protocol {version[1]:02x}{version[2]:02x})")
            return True
        finally:
            os.close(fd)
    return False


def main():
    if len(sys.argv) != 1:
        sys.exit("this helper takes no arguments")
    try:
        color = read_theme_color()
    except (OSError, ValueError, PermissionError, UnicodeDecodeError) as e:
        sys.exit(f"keyboard.rgb refused: {e}")
    if color is None:
        return  # theme defines no keyboard color; nothing to do
    if not apply_color(vivid(color)):
        sys.exit("no responding Keychron VIA interface (unplugged, wireless-only, or missing udev rule?)")


if __name__ == "__main__":
    main()
