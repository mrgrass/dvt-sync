"""Long-running loop: detect the recorder, sync when it appears, back off when it misbehaves."""
from __future__ import annotations

import asyncio
import fcntl
import logging
import time
from pathlib import Path

from .client import DeviceError
from .config import Config
from .presence import PresenceTracker, probe
from .store import Store
from .sync import Syncer

log = logging.getLogger("dvtsync.service")


class AlreadyRunning(Exception):
    pass


def acquire_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    f = open(state_dir / "dvtsync.lock", "w")  # noqa: SIM115 - held for the process lifetime
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise AlreadyRunning(f"another instance holds {f.name}") from None
    return f  # keep referenced for the process lifetime


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), max(seconds, 0.5))
    except TimeoutError:
        pass


async def run(cfg: Config, stop: asyncio.Event) -> None:
    lock = acquire_lock(cfg.state_dir)  # noqa: F841
    store = Store(cfg.state_dir / "dvtsync.db")
    syncer = Syncer(cfg, store)
    tracker = PresenceTracker(cfg.present_confirmations, cfg.absent_confirmations)
    next_sync = 0.0
    log.info("watching for %s:%d (recordings -> %s)", cfg.host, cfg.port, cfg.recordings_dir)
    try:
        while not stop.is_set():
            ip = await probe(cfg.host, cfg.port, cfg.connect_timeout)
            event = tracker.update(ip)
            if event is True:
                log.info("recorder arrived at %s", tracker.ip)
                next_sync = 0.0
            elif event is False:
                log.info("recorder left the network")
            if ip:
                store.set_kv("last_seen", str(time.time()))

            if tracker.present and time.time() >= next_sync and not stop.is_set():
                cycle = asyncio.create_task(_cycle(cfg, syncer, store, tracker.ip))
                stopper = asyncio.create_task(stop.wait())
                await asyncio.wait({cycle, stopper}, return_when=asyncio.FIRST_COMPLETED)
                stopper.cancel()
                if not cycle.done():  # shutdown requested mid-sync: abort (the .part file is removed)
                    cycle.cancel()
                    await asyncio.gather(cycle, return_exceptions=True)
                    break
                next_sync = cycle.result()

            if tracker.present:
                wait = min(cfg.present_interval, max(next_sync - time.time(), 1.0))
            else:
                wait = cfg.poll_interval
            await _sleep(stop, wait)
    finally:
        store.close()
        log.info("stopped")


async def _cycle(cfg: Config, syncer: Syncer, store: Store, ip: str | None) -> float:
    """Run one sync; return the epoch time of the next one."""
    now = time.time()
    try:
        rep = await syncer.run_once(ip)
    except (TimeoutError, DeviceError, OSError) as e:
        log.warning("sync failed: %s; cooling down %.0fs", e, cfg.failure_cooldown)
        store.set_kv("last_error", f"{time.strftime('%F %T')} {e}")
        return time.time() + cfg.failure_cooldown
    if rep.failed:
        return time.time() + cfg.failure_cooldown
    store.set_kv("last_sync_ok", str(time.time()))
    due = store.next_due_time()
    if rep.busy and due is not None:
        return time.time() + 60  # recorder is recording: check again soon, it will be idle later
    if due is not None:
        return max(due, now + 5)  # retries waiting on backoff
    return time.time() + cfg.rescan_interval
