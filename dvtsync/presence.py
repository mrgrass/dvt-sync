"""Presence detection: mDNS name -> IP, then a bare TCP connect to the WebSocket port.

The mDNS resolution alone is not enough (the resolver can return a stale cache
entry for a device that is off), so a TCP connect is the actual liveness check.
A bare connect+close without WebSocket upgrade is harmless for the server.
"""
from __future__ import annotations

import asyncio
import socket


async def probe(host: str, port: int, timeout: float) -> str | None:
    """Return the device IP if it accepts TCP connections, else None."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM), timeout
        )
        ip = infos[0][4][0]
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (TimeoutError, OSError, IndexError):
        return None
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return ip


class PresenceTracker:
    """Debounces probe results so a single lost mDNS/Wi-Fi packet doesn't flap state."""

    def __init__(self, present_after: int, absent_after: int):
        self.present_after, self.absent_after = present_after, absent_after
        self.present = False
        self.ip: str | None = None
        self._ok = self._bad = 0

    def update(self, ip: str | None) -> bool | None:
        """Feed a probe result. Returns True on arrival, False on departure, None otherwise."""
        if ip:
            self._ok, self._bad = self._ok + 1, 0
            self.ip = ip
            if not self.present and self._ok >= self.present_after:
                self.present = True
                return True
        else:
            self._ok, self._bad = 0, self._bad + 1
            if self.present and self._bad >= self.absent_after:
                self.present, self.ip = False, None
                return False
        return None
