#!/usr/bin/env python3
"""Unpack a Philips VoiceTracer firmware package (.UPG, SoC ZVMP500 / Zaram).

Usage: python3 tools/extract_upg.py DVT2019_V1.20.UPG output_dir

Format (reverse-engineered, undocumented):
  0x00  "ZVMP500 SYSPKG FILE -2014-02-14-" (32 bytes) + u32 count (=3)
  0x40  3 16-byte entries: "-DVT211-" | u32 offset | u32 size  (211, 411, 611)
  each image: "ZVMP500 SYSTEM FILE -2013-11-20-" (32 bytes), then a 16-byte header
        (u32 ver, u32 nfiles, u32 checksum not decoded, u32 1)
  file entries from +0x2c, 36-byte stride:
        u32 flag | char name[12] | u32 offset (relative to the image) | u32 size | 12-byte pad
"""
import os
import struct
import sys


def main(path, outdir):
    d = open(path, 'rb').read()
    assert d.startswith(b'ZVMP500 SYSPKG FILE'), 'not a ZVMP500 package'
    for k in range(3):
        o = 0x40 + k * 16
        tag = d[o:o+8].decode()
        off, sz = struct.unpack('<II', d[o+8:o+16])
        img = d[off:off+sz]
        n = struct.unpack('<I', img[0x24:0x28])[0]
        sub = os.path.join(outdir, tag.strip('-'))
        os.makedirs(sub, exist_ok=True)
        print(f'{tag}: offset {off:#x} size {sz} files {n}')
        p = 0x2c
        for _ in range(n):
            e = img[p:p+0x24]; p += 0x24
            name = e[4:16].rstrip(b'\0').decode()
            fo, fs = struct.unpack('<II', e[16:24])
            # FAT 8.3 names padded with spaces: "DEVICE  TXT" -> "DEVICE.TXT"
            fname = (name[:8].rstrip() + '.' + name[8:].rstrip()) if len(name) > 8 else name
            open(os.path.join(sub, fname), 'wb').write(img[fo:fo+fs])
            print(f'  {fname:14} {fs:8d}')

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
