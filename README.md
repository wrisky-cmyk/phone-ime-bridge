# Phone IME Bridge

Use your phone's keyboard or voice input to type text into the current focused Linux Wayland application.

Built for users who want to use phone's input method (with voice input ) on Linux desktops such as Niri, Hyprland, Sway, and other wlroots-based compositors.

## Features

- Use input method of phone
- Supports Chinese and other Unicode text
- Works through a local web page
- Pastes through the Wayland clipboard, typing directly where a paste shortcut cannot reach
- No Bluetooth required

## Requirements

- Linux Wayland session
- Phone and computer on the same LAN

On Arch Linux:

```bash
yay -S wtype crossmacro
```

`xdotool` is optional and only used for XWayland applications.

## Usage

Start the server:

```bash
python server.py
```


Find your computer IP:

```bash
ip -4 addr | grep inet
```

Open this on your phone:

```
http://YOUR_COMPUTER_IP:8765
```

Put your cursor in any input field on your computer, type or use voice input on your phone, then press send.

## Tested on
Arch Linux Niri
Arch Linux Hyprland
Android
wechat
qq
firefox
edge
chrome
vscode
kitty
thunar
yazi
cc-switch

## Input Behavior

### Supported

- Chinese text
- English text
- Spaces
- Multiple spaces
- Newlines

### Sending Rules

- Voice recognition completion → automatic send
- Enter → newline
- Ctrl+Enter → manual send

## Compatibility Notes

### How the text gets in

Clipboard paste is the default: the text goes on the Wayland clipboard, a paste
shortcut is synthesised, and the previous clipboard content is put back once the
paste has been served. The shortcut depends on the focused window:

| Target | Shortcut |
| --- | --- |
| terminals (kitty, ghostty, alacritty, ...) | `ctrl+shift+v` |
| wechat / qq | `crossmacro` |
| anything else | `ctrl+v` |

Direct text injection (`wtype <text>`) is only used where a paste shortcut is
known not to arrive at all:

- Gtk3 widgets. Verified on Hyprland 0.56 with Thunar 4.20 and with a minimal
  Gtk3 entry: `ctrl+a`, `ctrl+l`, `ctrl+q`, `ctrl+w` and `ctrl+v` are all no-ops
  there, while plain characters do arrive, so nothing can be pasted. Gtk4 clients
  (ghostty, zenity 4) and clients that handle keys themselves (firefox, chromium,
  alacritty) accept the same chords.
- Terminals keep pasting instead of typing: TUI applications that forward the
  kitty keyboard protocol (yazi, nvim, ...) drop the unicode keysyms `wtype`
  synthesises, so typed CJK never arrives.

### wechat and qq need `crossmacro`

`wtype`-driven paste has been reported to make wechat and qq windows exit
unexpectedly, so those two are sent through `crossmacro` instead. Without
`crossmacro` installed the code falls back to a `wtype` paste, which is exactly
the path the warning is about - install it if you send text to them.

### Observations on direct text injection

Measured on Arch Linux + Hyprland 0.56, with firefox 156.0.1 and chromium, in an
autofocused input field:

- the first CJK character was *not* dropped: `测试abc` arrived complete
- `ctrl+a` and `ctrl+v` work, so browsers keep the clipboard paste backend

Both observations are version dependent; older browser or toolkit builds may
still show the quirks the older notes in this README described.
