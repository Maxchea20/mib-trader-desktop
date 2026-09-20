"""Write desktop/src-tauri/icons/icon.ico so tauri-build can compile."""
import struct
import zlib
from pathlib import Path


def png(w, h, rgb=(13, 18, 27), accent=(201, 162, 39)):
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            m = min(x, y, w - 1 - x, h - 1 - y)
            if m < max(1, w // 10) or (w // 3 <= x < 2 * w // 3 and h // 3 <= y < 2 * h // 3):
                r, g, b = accent
            else:
                r, g, b = rgb
            row += bytes([r, g, b, 255])
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def ico_from_png(png_bytes, w, h):
    wb = 0 if w >= 256 else w
    hb = 0 if h >= 256 else h
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", wb, hb, 0, 0, 1, 32, len(png_bytes), 6 + 16)
    return header + entry + png_bytes


def main():
    here = Path(__file__).resolve().parent / "icons"
    here.mkdir(parents=True, exist_ok=True)
    p256 = png(256, 256)
    (here / "icon.png").write_bytes(p256)
    (here / "icon.ico").write_bytes(ico_from_png(p256, 256, 256))
    (here / "32x32.png").write_bytes(png(32, 32))
    (here / "128x128.png").write_bytes(png(128, 128))
    print("wrote", here / "icon.ico")


if __name__ == "__main__":
    main()
