#!/usr/bin/env python3
"""Probe for the Philips VoiceTracer DVT6110 WebSocket (ws://dvt.local:81/).

Connects, logs all raw traffic (text and binary) and sends a sequence of
read-only commands (no DeleteFile/StartRec/StopRec) to see what the device
actually answers.

Usage: uv run tools/probe.py
"""
import asyncio
import sys
from datetime import datetime

import websockets

URI = "ws://dvt.local:81/"
READ_ONLY_COMMANDS = [
    "GetDeviceState",
    "GetSerialNumber",
    "GetSettings",
    "GetFileList",
]


def log(direction: str, payload):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    if isinstance(payload, bytes):
        preview = payload[:64]
        print(f"[{ts}] {direction} BINARY len={len(payload)} preview={preview!r}")
    else:
        print(f"[{ts}] {direction} TEXT: {payload!r}")


async def receiver(ws, stop_event):
    try:
        async for msg in ws:
            log("<-", msg)
    except websockets.exceptions.ConnectionClosed as e:
        print(f"[conn] closed by peer: {e}")
    finally:
        stop_event.set()


async def main():
    print(f"Connecting to {URI} ...")
    async with websockets.connect(URI, open_timeout=10) as ws:
        print("[conn] connected")
        stop_event = asyncio.Event()
        recv_task = asyncio.create_task(receiver(ws, stop_event))

        # Let the unsolicited messages arrive (handshake "42", timeFromPVT)
        await asyncio.sleep(3)

        for cmd in READ_ONLY_COMMANDS:
            if stop_event.is_set():
                break
            print(f"\n--- sending command: {cmd} ---")
            await ws.send(cmd)
            log("->", cmd)
            await asyncio.sleep(2)

        await asyncio.sleep(2)
        recv_task.cancel()
        try:
            await recv_task
        except asyncio.CancelledError:
            pass

    print("\n[conn] closed cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
