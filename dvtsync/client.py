"""WebSocket client for the DVT6110 (ws://<host>:81/). Read-only: never deletes or records.

Facts confirmed on the real device (docs/protocol.md, 2026-11-04):
  * on connect: text "42", then "timeFromPVT", then "P" heartbeat every ~1 s (also mid-download!)
  * GetDeviceState / GetSerialNumber / GetSettings -> one JSON message
  * GetFileList -> one JSON message per file: {"Recordings": [ {filename, length_bytes, ...} ]}
  * DownloadFile works ONLY with buffersize "1024"; "4096" hung the device until power-cycled.
  * downloads arrive as binary frames; end is detected by byte count, never by "P".
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO

import websockets

from .config import Config

SAFE_BUFFERSIZE = "1024"  # do not change, see module docstring
_NOISE = {"42", "timeFromPVT", "P"}


class DeviceError(Exception):
    """Any failure talking to the recorder (timeout, closed connection, bad data)."""


@dataclass
class RemoteFile:
    path: str            # e.g. /C/VOICE/A/261104_100454_00.mp3 (also the DownloadFile path)
    length_bytes: int
    modified: str | None  # ISO local time from modified_* fields, if present
    raw: dict            # full metadata object exactly as sent by the device


def parse_recording(rec: dict) -> RemoteFile | None:
    name = rec.get("filename")
    if not isinstance(name, str) or not name:
        return None
    try:
        size = int(rec.get("length_bytes", 0))
    except (TypeError, ValueError):
        size = 0
    modified = None
    try:
        modified = datetime(*(int(rec[f"modified_{k}"]) for k in
                              ("year", "month", "day", "hour", "minute", "second"))).isoformat()
    except (KeyError, TypeError, ValueError):
        pass
    return RemoteFile(path=name, length_bytes=size, modified=modified, raw=rec)


@dataclass
class DownloadResult:
    bytes_received: int
    sha256: str


class Session:
    def __init__(self, ws, cfg: Config):
        self._ws, self._cfg = ws, cfg
        self._q: asyncio.Queue = asyncio.Queue()
        self._reader = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        # Continuous reader (same pattern that worked in tools/probe.py); the consumer pulls from a queue.
        try:
            async for msg in self._ws:
                self._q.put_nowait(msg)
        except websockets.exceptions.ConnectionClosed as e:
            self._q.put_nowait(e)
        except Exception as e:  # pragma: no cover - defensive
            self._q.put_nowait(e)
        else:
            self._q.put_nowait(websockets.exceptions.ConnectionClosedOK(None, None))

    async def _next(self, timeout: float):
        try:
            msg = await asyncio.wait_for(self._q.get(), timeout)
        except TimeoutError:
            raise DeviceError(f"timeout after {timeout}s") from None
        if isinstance(msg, Exception):
            raise DeviceError(f"connection lost: {msg!r}")
        return msg

    async def _handshake(self) -> None:
        await asyncio.sleep(self._cfg.handshake_wait)
        while not self._q.empty():
            m = self._q.get_nowait()
            if isinstance(m, Exception):
                raise DeviceError(f"connection lost during handshake: {m!r}")

    async def _json_reply(self, command: str) -> dict:
        await self._ws.send(command)
        deadline = asyncio.get_running_loop().time() + self._cfg.command_timeout
        while True:
            left = deadline - asyncio.get_running_loop().time()
            msg = await self._next(max(left, 0.01))
            if isinstance(msg, bytes) or msg in _NOISE:
                continue
            try:
                obj = json.loads(msg)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj

    async def device_state(self) -> str:
        return str((await self._json_reply("GetDeviceState")).get("Device State", "UNKNOWN"))

    async def serial(self) -> str | None:
        return (await self._json_reply("GetSerialNumber")).get("Serial")

    async def settings(self) -> dict:
        return await self._json_reply("GetSettings")

    async def list_files(self) -> list[RemoteFile]:
        """GetFileList streams one message per file; the burst ends after `list_quiet` of silence."""
        await self._ws.send("GetFileList")
        loop = asyncio.get_running_loop()
        files: dict[str, RemoteFile] = {}
        started = last = loop.time()
        while True:
            now = loop.time()
            if files and now - last >= self._cfg.list_quiet:
                break  # burst finished
            if not files and now - started >= self._cfg.command_timeout:
                break  # empty recorder (or silent): caller treats "no files" as harmless
            try:
                msg = await self._next(1.0)
            except DeviceError as e:
                if "timeout" in str(e):
                    continue
                raise
            if isinstance(msg, bytes) or msg in _NOISE:
                continue
            try:
                obj = json.loads(msg)
            except ValueError:
                continue
            if isinstance(obj, dict):
                for rec in obj.get("Recordings", []) or []:
                    rf = parse_recording(rec) if isinstance(rec, dict) else None
                    if rf:
                        files[rf.path] = rf
                        last = loop.time()
        return list(files.values())

    async def download(self, remote_path: str, expected: int, out: BinaryIO,
                       on_progress: Callable[[int], None] | None = None) -> DownloadResult:
        """Stream `expected` bytes (rounded up to the last 1024 frame) into `out`.

        Stops as soon as received >= expected. Raises DeviceError on stall/disconnect.
        """
        if expected <= 0:
            raise DeviceError("unknown file size: end of transfer cannot be detected")
        await self._ws.send(json.dumps({"DownloadFile": {"path": remote_path,
                                                         "buffersize": SAFE_BUFFERSIZE}}))
        h, total = hashlib.sha256(), 0
        loop = asyncio.get_running_loop()
        last_data = loop.time()
        while total < expected:
            # Heartbeats ("P") arrive every ~1 s even when no data flows, so the stall clock
            # must be measured from the last binary frame, not from the last message.
            left = self._cfg.stall_timeout - (loop.time() - last_data)
            if left <= 0:
                raise DeviceError(f"stalled: no data for {self._cfg.stall_timeout:.0f}s "
                                  f"({total}/{expected} bytes)")
            try:
                msg = await self._next(left)
            except DeviceError as e:
                if "timeout" in str(e):
                    continue
                raise
            if isinstance(msg, bytes):
                last_data = loop.time()
                out.write(msg)
                h.update(msg)
                total += len(msg)
                if on_progress:
                    on_progress(total)
        out.flush()
        return DownloadResult(total, h.hexdigest())


@asynccontextmanager
async def connect(cfg: Config, host: str | None = None) -> AsyncIterator[Session]:
    """One connection per operation (multi-file reuse is untested on the real device)."""
    uri = f"ws://{host or cfg.host}:{cfg.port}/"
    try:
        # ping_interval=None: the ESP8266 server does not need/like library keepalives.
        ws = await websockets.connect(uri, open_timeout=10, max_size=None, ping_interval=None)
    except (TimeoutError, OSError, websockets.exceptions.WebSocketException) as e:
        raise DeviceError(f"cannot connect to {uri}: {e!r}") from e
    session = Session(ws, cfg)
    try:
        await session._handshake()
        yield session
    except websockets.exceptions.WebSocketException as e:
        raise DeviceError(f"websocket error: {e!r}") from e
    finally:
        session._reader.cancel()
        try:
            await asyncio.wait_for(ws.close(), 5)
        except Exception:
            pass
