#!/usr/bin/env python3
"""Test download of one audio file from the DVT6110 over WebSocket.

Exercises DownloadFile: sends the JSON request, collects binary frames until
the expected size is reached, saves the result and checks that the size
matches length_bytes and that the MP3 header looks plausible.

Usage: uv run tools/download_test.py <device_path> <length_bytes> <local_file_name>
"""
import asyncio
import json
import sys
from datetime import datetime

import websockets

URI = "ws://dvt.local:81/"


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}")


async def main(remote_path, expected_size, outname):
    chunks = []
    total = 0

    async with websockets.connect(URI, open_timeout=10, max_size=None) as ws:
        log("connected")

        async def drain_initial():
            # drain the initial handshake ("42", "timeFromPVT", periodic "P")
            for _ in range(3):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.5)
                    log(f"init <- {msg!r}")
                except TimeoutError:
                    break

        await drain_initial()

        req = {"DownloadFile": {"path": remote_path, "buffersize": "1024"}}
        payload = json.dumps(req)
        log(f"-> {payload}")
        await ws.send(payload)

        done = False
        last_log = asyncio.get_event_loop().time()
        idle_heartbeats = 0
        # "P" is both the periodic heartbeat (~1/s, always) and the end-of-transfer
        # marker: treat it as the end only once the received data has reached
        # the expected size.
        while not done:
            msg = await asyncio.wait_for(ws.recv(), timeout=30)
            if isinstance(msg, bytes):
                chunks.append(msg)
                total += len(msg)
                idle_heartbeats = 0
                now = asyncio.get_event_loop().time()
                if now - last_log > 1:
                    log(f"received {total}/{expected_size} bytes")
                    last_log = now
            else:
                log(f"<- TEXT {msg!r}")
                if msg == "P":
                    if total >= expected_size:
                        log(f"transfer complete, {total} bytes total")
                        done = True
                    elif total == 0:
                        idle_heartbeats += 1
                        if idle_heartbeats > 5:
                            log("too many heartbeats without data, giving up")
                            done = True
                    # otherwise: normal heartbeat mid-transfer, ignore

    data = b"".join(chunks)
    with open(outname, "wb") as f:
        f.write(data)

    print(f"\nSaved {outname}: {len(data)} bytes (expected {expected_size})")
    print(f"First 16 bytes: {data[:16]!r}")
    if data[:3] == b"ID3" or data[:2] == b"\xff\xfb" or data[:2] == b"\xff\xf3":
        print("MP3 header looks plausible.")
    else:
        print("WARNING: header not recognised as MP3/ID3.")


if __name__ == "__main__":
    remote_path = sys.argv[1]
    expected_size = int(sys.argv[2])
    outname = sys.argv[3]
    asyncio.run(main(remote_path, expected_size, outname))
