# Omakeychron

Your Keychron keyboard, wearing your Omarchy theme.

Every time you switch themes, Omakeychron pushes the theme's `keyboard.rgb` color to your wired Keychron over the VIA raw-HID protocol and saves it to the keyboard's onboard memory. No OpenRGB, no background daemon, no configuration — the keyboard simply follows the theme, and because the color is stored on the keyboard itself, it survives replugs and stays on when you go wireless.

Colors are adjusted before they reach the LEDs: theme palettes are often desaturated, which renders washed-out and blue-shifted on RGB hardware (jade turns turquoise). Omakeychron boosts saturation and brightness and pulls the green–cyan band back toward true green, so the keyboard shows the color the theme *means*. Near-gray theme colors are passed through untouched.

## Supported keyboards

Any Keychron running QMK/VIA firmware (USB vendor ID `3434`) that exposes the VIA raw-HID interface — the V, Q, K Pro/Max lines. Tested on a K3 Max and on a Q1 Pro running firmware 1.02 (VIA protocol v10).

**Wired only.** The Keychron Link 2.4 GHz dongle exposes a VIA interface but does not bridge it to the keyboard, and Bluetooth has no HID access at all. The plugin detects the dongle's dead interface and skips it. Practical upshot: plug in USB when you switch themes; the saved color then persists while you use the keyboard wirelessly.

## Install

```
omarchy plugin add https://github.com/paulsp94/omakeychron --enable
```

### One-time setup: udev rule (required)

Raw HID access to the keyboard needs a udev rule. This is a manual, one-time step — the plugin never runs anything privileged itself:

```
sudo tee /etc/udev/rules.d/70-keychron-via.rules <<'EOF'
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="3434", TAG+="uaccess"
EOF
sudo udevadm control --reload
sudo udevadm trigger
```

`TAG+="uaccess"` grants access only to the user physically logged in at the seat — not to every process on the system the way `MODE="0666"` would. Replug the keyboard once after installing the rule.

## Usage

There is none — that's the point. Switch themes (`omarchy-theme-set`, or the theme menu) and the keyboard follows.

Notes on behavior:

- The plugin applies the color on **theme change**, not on shell restart or boot: the keyboard already holds the last color in onboard memory, and re-writing its flash on every restart would wear it for nothing.
- If the keyboard is unplugged (or wireless) during a theme switch, that switch is skipped — plug in and switch themes to re-sync.
- Themes without a `keyboard.rgb` file are silently ignored; the keyboard keeps its current color.
- Two consecutive themes with the identical accent color won't re-trigger (Omarchy signals theme changes through the accent).

Troubleshooting lives in the journal: `journalctl --user -b | grep omakeychron`.

## What it does and doesn't do

- **No network.** The plugin makes no network requests of any kind.
- **No files written.** It writes nothing to disk — no state, no cache, no logs (diagnostics go to the journal via the shell).
- **Reads one file**: `~/.local/state/omarchy/current/theme/keyboard.rgb` (bounded, symlink-refusing descriptor read), plus the read-only sysfs metadata under `/sys/class/hidraw/` to find the keyboard.
- **Writes to one device**: the Keychron's `/dev/hidraw*` VIA interface (validated by a protocol handshake before any write). The color is saved to the keyboard's onboard memory — this is a deliberate, disclosed hardware write that happens once per theme change.
- **No privileges.** Nothing runs as root; the udev rule above is documented, not installed by the plugin.

## Removing

```
omarchy plugin remove io.github.paulsp94.omakeychron
```

What persists after removal, and how to remove it:

- **The udev rule** (`/etc/udev/rules.d/70-keychron-via.rules`), because it was installed manually:
  ```
  sudo rm /etc/udev/rules.d/70-keychron-via.rules
  sudo udevadm control --reload
  ```
- **The last color in the keyboard's onboard memory.** The keyboard keeps showing it; change it any time in [VIA](https://usevia.app) or with the keyboard's lighting hotkeys.

The plugin itself stores nothing — no state files, caches or credentials anywhere on disk.

## License

MIT
