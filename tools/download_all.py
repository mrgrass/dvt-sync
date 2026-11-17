#!/usr/bin/env python3
"""Download every file from the Philips VoiceTracer DVT6110 (ws://dvt.local:81/).

Read-only commands only (GetFileList, DownloadFile). Never deletes anything.
buffersize is ALWAYS "1024": other values have hung the device.
One new connection per file; files already present with size
>= length_bytes are skipped.

Standalone predecessor of the dvtsync service.

Usage: uv run --with websockets tools/download_all.py [output_dir] [--list]
"""
import asyncio
import json
import sys
from pathlib import Path

import websockets

URI = "ws://dvt.local:81/"


async def reader(ws, q):
    """Read continuously (same pattern as tools/probe.py, which works) into a queue."""
    try:
        async for msg in ws:
            await q.put(msg)
    except websockets.exceptions.ConnectionClosed as e:
        await q.put(e)


async def get_file_list():
    files = []
    q = asyncio.Queue()
    async with websockets.connect(URI, open_timeout=10, max_size=None, ping_interval=None) as ws:
        task = asyncio.create_task(reader(ws, q))
        await asyncio.sleep(3)  # handshake "42", timeFromPVT, heartbeat "P"
        await ws.send("GetFileList")
        # one JSON message per file; the burst ends when nothing arrives for 3s
        loop = asyncio.get_running_loop()
        last = loop.time()
        while loop.time() - last < 3:  # "P" arrives every 1s: it does not count
            try:
                msg = await asyncio.wait_for(q.get(), timeout=1.5)
            except TimeoutError:
                continue
            if isinstance(msg, Exception):
                raise msg
            if isinstance(msg, bytes):
                continue
            try:
                obj = json.loads(msg)
            except ValueError:
                continue
            if isinstance(obj, dict):
                for rec in obj.get("Recordings", []):
                    if "filename" in rec:
                        files.append(rec)
                        last = loop.time()
        task.cancel()
    return files


async def download(remote_path, expected, dest: Path):
    chunks, total = [], 0
    q = asyncio.Queue()
    async with websockets.connect(URI, open_timeout=10, max_size=None, ping_interval=None) as ws:
        task = asyncio.create_task(reader(ws, q))
        await asyncio.sleep(3)  # initial handshake
        while not q.empty():
            q.get_nowait()
        await ws.send(json.dumps({"DownloadFile": {"path": remote_path, "buffersize": "1024"}}))
        while total < expected:
            msg = await asyncio.wait_for(q.get(), timeout=30)
            if isinstance(msg, Exception):
                raise msg
            if isinstance(msg, bytes):
                chunks.append(msg)
                total += len(msg)
        await asyncio.sleep(0.5)
        task.cancel()
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(b"".join(chunks))
    tmp.rename(dest)
    return total


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0] if args else "recordings")
    out.mkdir(parents=True, exist_ok=True)

    files = await get_file_list()
    print(f"{len(files)} files on the recorder")
    for f in files:
        print(json.dumps(f, ensure_ascii=False))
    if "--list" in sys.argv:
        return

    for i, f in enumerate(files, 1):
        name = f["filename"]
        size = int(f.get("length_bytes", 0))
        dest = out / Path(name).name
        if dest.exists() and dest.stat().st_size >= size > 0:
            print(f"[{i}/{len(files)}] skipping {dest.name} (already downloaded)")
            continue
        print(f"[{i}/{len(files)}] downloading {name} ({size} bytes)")
        got = await download(name, size, dest)
        print(f"    ok: {got} bytes -> {dest}")
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
