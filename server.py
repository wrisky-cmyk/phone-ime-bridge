#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import json

HOST = "0.0.0.0"
PORT = 8765
WEB_DIR = Path(__file__).parent / "web"

# The focused app fetches the selection asynchronously, so the previous
# clipboard content must not be put back before the paste was served.
PASTE_SETTLE_SECONDS = 0.3

IM_APPS = ("wechat", "weixin", "qq")
BROWSER_APPS = ("firefox", "chromium", "chrome", "brave", "edge", "zen")
TERMINAL_APPS = (
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
)

# Fallback for clients whose /proc entry we cannot read; gtk3 clients are
# normally detected at runtime by is_gtk3_client().
TYPED_APPS = ("thunar", "cc-switch")

# One paste at a time: the clipboard dance in send_by_clipboard() is not
# safe to interleave, and request threads share the clipboard.
_send_lock = threading.Lock()


def app_id_matches(app_id: str, names) -> bool:
    """Match an app id exactly or as a whole word, so "zen" != "zenity"."""
    tokens = re.split(r"[-_.]", app_id)
    return any(name == app_id or name in tokens for name in names)


def is_gtk3_client(pid) -> bool:
    """Whether the window's process links gtk3.

    gtk3 clients ignore the ctrl chords wtype synthesises (verified with
    thunar 4.20 on hyprland 0.56: ctrl+a, ctrl+l, ctrl+q, ctrl+w and ctrl+v
    are all no-ops while plain characters do arrive), so a paste never
    reaches them and the text has to be typed instead.
    """
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/maps", "rb") as maps:
            return b"libgtk-3.so" in maps.read()
    except OSError:
        return False


def types_text_directly(win) -> bool:
    if app_id_matches(win["app_id"], TYPED_APPS):
        return True
    return is_gtk3_client(win.get("pid"))


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
                        "pid": win.get("pid"),
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
                    "pid": data.get("pid"),
                }
        except Exception as e:
            print("get focused window (hyprland) failed:", e)

    return {"app_id": "", "title": "", "xwayland": False, "pid": None}


def detect_mode(win=None):
    if win is None:
        win = get_focused_window()
    app_id = win["app_id"]
    title = win["title"]

    print("focused:", app_id, "|", title, "| xwayland:", win.get("xwayland", False))

    if app_id_matches(app_id, IM_APPS):
        return "im"

    if app_id_matches(app_id, BROWSER_APPS):
        return "browser"

    if app_id_matches(app_id, TERMINAL_APPS):
        return "terminal"

    # Some im clients do not report a usable app id. Only fall back to the
    # window title after the classes above had their chance, so a browser tab
    # titled "QQ mail" is not mistaken for the im app.
    if any(x in title for x in ["微信", "qq"]):
        return "im"

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
    xwayland = bool(win.get("xwayland", False))
    use_xdotool = xwayland and shutil.which("xdotool") is not None

    if mode == "normal" and types_text_directly(win):
        # These clients drop the synthesised ctrl chords, so ctrl+v never
        # fires and pasting would silently do nothing. Typing does reach them:
        # plain characters arrive even where ctrl+v does not.
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
        body = self.rfile.read(length).decode("utf-8", errors="replace")

        text = parse_qs(body).get("text", [""])[0]

        if text != "":
            with _send_lock:
                send_text(text)

        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print(f"Phone IME Bridge running on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
