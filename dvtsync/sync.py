"""One sync cycle: list -> record in manifest -> download what is due. Never deletes anything."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .client import DeviceError, RemoteFile, connect
from .config import Config
from .id3 import read_bookmarks
from .store import Row, Store

log = logging.getLogger("dvtsync.sync")
SIDECAR_SCHEMA = 1


class LocalError(Exception):
    """Problem on our side (disk full, permissions): not the device's fault."""


@dataclass
class Report:
    seen: int = 0
    new: int = 0
    downloaded: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    busy: bool = False       # recorder not idle: downloads postponed
    failed: bool = False     # a transfer failed: caller should back off


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Syncer:
    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store
        cfg.recordings_dir.mkdir(parents=True, exist_ok=True)

    async def run_once(self, ip: str | None = None) -> Report:
        rep = Report()
        async with connect(self.cfg, ip) as s:
            state = await s.device_state()
            serial = await s.serial()
            settings = await s.settings()
            files = await s.list_files()
        device = {"serial": serial, "settings": settings}
        self.store.set_kv("device", json.dumps(device))
        rep.seen = len(files)
        for rf in files:
            if self._observe(rf) == "new":
                rep.new += 1
        self.store.set_kv("last_listing", str(time.time()))
        log.info("recorder state=%s serial=%s files=%d new=%d", state, serial, rep.seen, rep.new)

        if state != "STOP":
            rep.busy = True
            log.info("recorder busy (%s): downloads postponed", state)
            return rep

        for row in self.store.due():
            self._adopt_if_present(row, device, rep)
        for row in self.store.due():
            try:
                await self._download(row, ip, device, rep)
            except DeviceError as e:
                st = self.store.mark_failed(row.remote_path, str(e), self.cfg.max_attempts,
                                            self.cfg.backoff_base, self.cfg.backoff_max)
                log.warning("download of %s failed (%s) -> %s", row.remote_path, e, st)
                rep.failed = True
                break  # leave the fragile device alone; caller applies a cooldown
            except LocalError as e:
                log.error("local error on %s: %s", row.remote_path, e)
                rep.failed = True
                break
            await asyncio.sleep(self.cfg.inter_file_pause)
        return rep

    # ------------------------------------------------------------------
    def _observe(self, rf: RemoteFile) -> str:
        name = self.store.local_name_for(rf.path)
        return self.store.observe(rf.path, name, rf.length_bytes, rf.modified, rf.raw)

    def _adopt_if_present(self, row: Row, device: dict, rep: Report) -> None:
        """A complete file already on disk (crash between rename and DB commit, or recordings
        fetched by the earlier scripts) is registered instead of downloaded again."""
        dest = self.cfg.recordings_dir / row.local_name
        if not dest.is_file() or row.length_bytes <= 0 or dest.stat().st_size < row.length_bytes:
            return
        sha = _sha256(dest)
        self._write_sidecar(row, dest.stat().st_size, sha, device)
        self.store.adopt(row.remote_path, dest.stat().st_size, sha)
        rep.adopted.append(row.local_name)
        log.info("adopted existing file %s", row.local_name)

    async def _download(self, row: Row, ip: str | None, device: dict, rep: Report) -> None:
        cfg = self.cfg
        free = shutil.disk_usage(cfg.recordings_dir).free
        if free < cfg.min_free_bytes + 2 * row.length_bytes:
            raise LocalError(f"not enough disk space ({free} bytes free)")
        dest = cfg.recordings_dir / row.local_name
        part = dest.with_name(dest.name + ".part")
        log.info("downloading %s (%d bytes)", row.remote_path, row.length_bytes)
        t0 = time.monotonic()
        try:
            async with connect(cfg, ip) as s:
                # Re-check right before transferring: the user may have started a recording.
                state = await s.device_state()
                if state != "STOP":
                    rep.busy = True
                    raise DeviceError(f"recorder became busy ({state})")
                with open(part, "wb") as f:
                    res = await s.download(row.remote_path, row.length_bytes, f)
                    os.fsync(f.fileno())
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        os.replace(part, dest)
        _fsync_dir(cfg.recordings_dir)
        self._write_sidecar(row, res.bytes_received, res.sha256, device)
        self.store.mark_downloaded(row.remote_path, res.bytes_received, res.sha256)
        rep.downloaded.append(row.local_name)
        log.info("downloaded %s: %d bytes in %.0fs", row.local_name, res.bytes_received,
                 time.monotonic() - t0)

    def _bookmarks(self, row: Row) -> tuple[list[int], list[int]]:
        try:
            dur = int(row.metadata["length"])  # milliseconds on the real device
        except (KeyError, TypeError, ValueError):
            dur = None
        try:
            return read_bookmarks(self.cfg.recordings_dir / row.local_name, dur)
        except OSError:
            return [], []

    def _write_sidecar(self, row: Row, received: int, sha: str, device: dict) -> None:
        bm = self._bookmarks(row)
        doc = {
            "schema": SIDECAR_SCHEMA,
            "audio_file": row.local_name,
            "remote_path": row.remote_path,
            "recorded_at": row.modified,        # device clock, local time, naive
            "length_bytes": row.length_bytes,   # as declared by the device
            "bytes_received": received,         # may exceed length_bytes (see docs/protocol.md)
            "sha256": sha,
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "device": device,                   # serial + settings snapshot at sync time
            "metadata": row.metadata,           # GetFileList entry, verbatim
            "bookmarks_ms": bm[0],              # from the MP3's Philips PRIV frame (heuristic,
            "bookmarks_raw": bm[1],             # decoded from 1 sample: see dvtsync/id3.py)
        }
        self.store.set_bookmarks(row.remote_path, doc["bookmarks_ms"])
        _atomic_write(self.cfg.recordings_dir / (row.local_name + ".json"),
                      json.dumps(doc, indent=2, ensure_ascii=False).encode())

    def backfill(self) -> int:
        """Rewrite sidecars/bookmarks for files already downloaded (no device access)."""
        n = 0
        device = json.loads(self.store.get_kv("device") or "{}")
        for r in self.store.all():
            if r["status"] != "downloaded":
                continue
            row = self.store._row(r)
            dest = self.cfg.recordings_dir / row.local_name
            if dest.is_file():
                self._write_sidecar(row, r["bytes_received"], r["sha256"], device)
                n += 1
        return n
