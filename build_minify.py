"""Minifie JS et CSS pour rendre le code illisible en Ctrl+U."""
import os
import re

try:
    import rjsmin
except ImportError:
    rjsmin = None
try:
    import rcssmin
except ImportError:
    rcssmin = None

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")


def minify_js(path: str) -> None:
    if not rjsmin:
        print("rjsmin non installe: pip install rjsmin")
        return
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    out = rjsmin.jsmin(raw)
    out_path = path.replace(".js", ".min.js")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"  -> {os.path.basename(out_path)}")


def minify_css(path: str) -> None:
    if not rcssmin:
        print("rcssmin non installe: pip install rcssmin")
        return
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    out = rcssmin.cssmin(raw)
    out_path = path.replace(".css", ".min.css")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"  -> {os.path.basename(out_path)}")


def main():
    print("Minification JS...")
    for name in [
        "student.js",
        "music-poll.js",
        "admin.js",
        "bus-diagnostics.js",
        "display-announcements.js",
        "bus-board-render.js",
        "bus-board-pages.js",
        "display.js",
        "tv.js",
        "displaybus.js",
    ]:
        p = os.path.join(STATIC, "js", name)
        if os.path.exists(p):
            minify_js(p)
    print("Minification CSS...")
    p = os.path.join(STATIC, "css", "style.css")
    if os.path.exists(p):
        minify_css(p)
    print("Termine.")


if __name__ == "__main__":
    main()
