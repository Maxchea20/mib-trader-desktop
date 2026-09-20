import struct, zlib
from pathlib import Path

w = h = 32
rows = []
for y in range(h):
    row = bytearray([0])
    for x in range(w):
        row += bytes([201, 162, 39, 255])
    rows.append(bytes(row))
raw = b"".join(rows)

def chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

png = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(raw, 9))
    + chunk(b"IEND", b"")
)
hdr = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), 22)

out = Path(__file__).resolve().parent / "icons"
out.mkdir(exist_ok=True)
(out / "icon.ico").write_bytes(hdr + png)
print("ok", out / "icon.ico")