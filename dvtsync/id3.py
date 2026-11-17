"""Read bookmarks from the Philips private ID3 frame embedded in the recorder's MP3 files.

REVERSE-ENGINEERED from two samples with bookmarks set by the user at known times:
  261104_210100_00.mp3  (9 s, 15 s)            -> decoded 9.2 s, 15.4 s
  261116_194424_00.mp3  (35, 50, 70, 90 s)     -> decoded 35.4, 50.4, 70.5, 90.4 s
PRIV frame, owner "http://www.philips.com/dictation". From frame-body offset 67, slots of 4 bytes:
a 3-byte BIG-endian position in milliseconds + 1 zero byte; the list ends at the first all-zero slot.
Not yet verified: more than 4 bookmarks, files longer than the ones above.
"""
from __future__ import annotations

import struct
from pathlib import Path

OWNER = b"http://www.philips.com/dictation"
BOOKMARK_OFFSET = 67  # offset inside the PRIV frame body (owner string included)


def read_bookmarks(path: Path, duration_ms: int | None = None) -> tuple[list[int], list[str]]:
    """Return (positions_ms, raw_slots_hex); both empty if none or if the layout looks wrong."""
    with open(path, "rb") as f:
        head = f.read(10)
        if head[:3] != b"ID3" or head[3] != 3:      # only ID3v2.3 seen on this device
            return [], []
        size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        tag = f.read(size)
    i = 0
    while i + 10 <= len(tag) and tag[i] != 0:
        fid, n = tag[i:i + 4], struct.unpack(">I", tag[i + 4:i + 8])[0]
        body = tag[i + 10:i + 10 + n]
        i += 10 + n
        if fid == b"PRIV" and body.startswith(OWNER):
            ms: list[int] = []
            raw: list[str] = []
            for k in range(BOOKMARK_OFFSET, len(body) - 3, 4):
                slot = body[k:k + 4]
                if not any(slot):
                    break
                if slot[3] != 0:
                    return [], []  # layout assumption broken
                ms.append((slot[0] << 16) | (slot[1] << 8) | slot[2])
                raw.append(slot.hex())
            if duration_ms is not None and any(m > duration_ms for m in ms):
                return [], []  # implausible: position beyond end of the recording
            return ms, raw
    return [], []


def read_bookmarks_ms(path: Path, duration_ms: int | None = None) -> list[int]:
    return read_bookmarks(path, duration_ms)[0]
