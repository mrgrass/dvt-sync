"""Minimal imitation of the DVT6110 WebSocket server, based on the behaviour confirmed in docs/protocol.md."""
import asyncio
import json

import websockets


class FakeDevice:
    def __init__(self, files: dict[str, bytes], state="STOP"):
        self.files, self.state = files, state
        self.download_requests: list[dict] = []
        self.silent_download = False          # accept DownloadFile but send only heartbeats
        self.break_after: int | None = None   # close the socket after N bytes of a download
        self.server = None
        self.port = 0

    async def start(self):
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0, ping_interval=None)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    def meta(self, path):
        return {"filename": path, "scene": "MEETING", "recordingformats": "MP3_128",
                "modified_year": 2026, "modified_month": 11, "modified_day": 4,
                "modified_hour": 10, "modified_minute": 4, "modified_second": 54,
                "length": 120000, "length_bytes": str(len(self.files[path]))}

    async def _heartbeat(self, ws):
        try:
            while True:
                await asyncio.sleep(0.05)
                await ws.send("P")
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _handler(self, ws):
        await ws.send("42")
        await ws.send("timeFromPVT")
        hb = asyncio.create_task(self._heartbeat(ws))
        try:
            async for msg in ws:
                if msg == "GetDeviceState":
                    await ws.send(json.dumps({"Device State": self.state}))
                elif msg == "GetSerialNumber":
                    await ws.send(json.dumps({"Serial": "FAKE0001"}))
                elif msg == "GetSettings":
                    await ws.send(json.dumps({"folder": "FOLDER_A", "noisecut": "NOISECUT_OFF"}))
                elif msg == "GetFileList":
                    for p in self.files:
                        await ws.send(json.dumps({"Recordings": [self.meta(p)]}))
                elif msg.startswith("{"):
                    req = json.loads(msg)["DownloadFile"]
                    self.download_requests.append(req)
                    assert req["buffersize"] == "1024"
                    if self.silent_download:
                        continue
                    data = self.files[req["path"]]
                    padded = data + b"\x55" * (-len(data) % 1024)  # last frame is padded
                    for i in range(0, len(padded), 1024):
                        if self.break_after is not None and i >= self.break_after:
                            await ws.close()
                            return
                        await ws.send(padded[i:i + 1024])
                        await asyncio.sleep(0)
        finally:
            hb.cancel()
