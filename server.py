#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs
from pathlib import Path
import shutil
import subprocess
import time
import json

HOST = "0.0.0.0"
PORT = 8765
WEB_DIR = Path(__file__).parent / "web"

# The focused app fetches the selection asynchronously, so the previous
# clipboard content must not be put back before the paste was served.
PASTE_SETTLE_SECONDS = 0.3

# Clients that ignore the ctrl chords wtype synthesises, so the paste shortcut
# never fires there. Verified with thunar 4.20 (gtk3) on hyprland 0.56:
# ctrl+a, ctrl+l, ctrl+q, ctrl+w and ctrl+v are all no-ops while plain
# characters do arrive, so type the text instead of pasting it.
TYPED_APPS = ("thunar", "cc-switch")


def get_focused_window():
    # Niri
    if shutil.which("niri"):
        try:
            r = subprocess.run(
                ["niri", "msg", "--json", "windows"],
                text=True,
                capture_output=True,
                check=True,
            )
            for win in json.loads(r.stdout):
                if win.get("is_focused"):
                    return {
                        "app_id": (win.get("app_id") or "").lower(),
                        "title": (win.get("title") or "").lower(),
                        "xwayland": bool(win.get("is_x11", False)),
                    }
        except Exception as e:
            print("get focused window (niri) failed:", e)

    # Hyprland
    if shutil.which("hyprctl"):
        try:
            r = subprocess.run(
                ["hyprctl", "activewindow", "-j"],
                text=True,
                capture_output=True,
                check=True,
            )
            data = json.loads(r.stdout)
            if isinstance(data, dict):
                return {
                    "app_id": (data.get("class") or data.get("initialClass") or "").lower(),
                    "title": (data.get("title") or "").lower(),
                    "xwayland": bool(data.get("xwayland", False)),
                }
        except Exception as e:
            print("get focused window (hyprland) failed:", e)

    return {"app_id": "", "title": "", "xwayland": False}


def detect_mode(win=None):
    if win is None:
        win = get_focused_window()
    app_id = win["app_id"]
    title = win["title"]

    print("focused:", app_id, "|", title, "| xwayland:", win.get("xwayland", False))

    if any(x in app_id for x in ["wechat", "weixin", "qq"]) or any(
        x in title for x in ["微信", "qq"]
    ):
        return "im"

    if any(x in app_id for x in ["firefox", "chromium", "chrome", "brave", "edge", "zen"]):
        return "browser"

    if any(x in app_id for x in [
        "foot",
        "kitty",
        "ghostty",
        "alacritty",
        "wezterm",
        "terminator",
        "gnome-terminal",
        "org.gnome.terminal",
        "x-terminal-emulator",
        "xfce4-terminal",
        "konsole",
    ]):
        return "terminal"
    return "normal"


def get_clipboard():
    # None means "no clipboard text", e.g. the clipboard holds an image.
    r = subprocess.run(
        ["wl-paste", "--no-newline", "--type", "text"],
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def set_clipboard(text: str):
    subprocess.run(
        ["wl-copy"],
        input=text,
        text=True,
        check=False,
        # wl-copy forks a daemon that owns the selection; keep it from
        # inheriting (and holding open) our stdout/stderr.
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def paste_with_wtype(shift: bool = False):
    release = ["-m", "shift", "-m", "ctrl"] if shift else ["-m", "ctrl"]
    press = ["-M", "ctrl", "-M", "shift"] if shift else ["-M", "ctrl"]
    subprocess.run(
        ["wtype", *press, "-k", "v", *release],
        check=False,
    )


def paste_with_xdotool(shift: bool = False):
    keys = ["ctrl+shift+v"] if shift else ["ctrl+v"]
    subprocess.run(
        ["xdotool", "key", "--clearmodifiers", *keys],
        check=False,
    )


def paste_with_crossmacro():
    try:
        subprocess.run(
            ["crossmacro", "run", "--step", "tap ctrl+v"],
            check=False,
        )
    except FileNotFoundError:
        print("crossmacro not found, falling back to wtype paste")
        paste_with_wtype()


def send_by_clipboard(text: str, paste_func):
    old = get_clipboard()

    set_clipboard(text)
    paste_func()
    time.sleep(PASTE_SETTLE_SECONDS)
    # Restore only if the clipboard still holds our text: the user may have
    # copied something else while we were pasting.
    if old is not None and get_clipboard() == text:
        set_clipboard(old)


def send_by_wtype(text: str):
    subprocess.run(
        ["wtype", text],
        check=False,
    )


def send_text(text: str):
    win = get_focused_window()
    mode = detect_mode(win)
    app_id = win["app_id"]
    xwayland = bool(win.get("xwayland", False))
    use_xdotool = xwayland and shutil.which("xdotool") is not None

    if any(x in app_id for x in TYPED_APPS):
        send_by_wtype(text)
    elif mode == "terminal":
        # wtype types text through the keymap, and terminals that forward the
        # kitty keyboard protocol (yazi, nvim, ...) drop the unicode keysyms
        # wtype synthesises. Paste with ctrl+shift+v instead, like terminals
        # under XWayland already do.
        if use_xdotool:
            send_by_clipboard(text, lambda: paste_with_xdotool(shift=True))
        else:
            send_by_clipboard(text, lambda: paste_with_wtype(shift=True))
    elif mode == "im":
        if use_xdotool:
            send_by_clipboard(text, paste_with_xdotool)
        else:
            send_by_clipboard(text, paste_with_crossmacro)
    else:
        if use_xdotool:
            send_by_clipboard(text, paste_with_xdotool)
        else:
            send_by_clipboard(text, paste_with_wtype)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = WEB_DIR / "index.html"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        text = parse_qs(body).get("text", [""])[0]

        if text!="":
            send_text(text)

        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print(f"Phone IME Bridge running on http://{HOST}:{PORT}")
    HTTPServer((HOST, PORT), Handler).serve_forever()
