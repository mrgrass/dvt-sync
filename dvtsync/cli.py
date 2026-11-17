from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time

from . import config as config_mod
from .service import AlreadyRunning, acquire_lock, run
from .store import Store
from .sync import Syncer


def _fmt(ts) -> str:
    return time.strftime("%F %T", time.localtime(float(ts))) if ts else "-"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dvtsync")
    ap.add_argument("-c", "--config", help="TOML config file")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the presence-watching service")
    sub.add_parser("once", help="sync once now (recorder must be reachable)")
    sub.add_parser("status", help="show manifest summary")
    sub.add_parser("list", help="list files known to the manifest")
    sub.add_parser("backfill", help="re-read bookmarks from downloaded files into sidecars/manifest")
    sub.add_parser("retry", help="reset permanently failed files to pending")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    cfg = config_mod.load(args.config)

    if args.cmd == "run":
        return asyncio.run(_run_service(cfg))
    if args.cmd == "once":
        try:
            lock = acquire_lock(cfg.state_dir)  # noqa: F841
        except AlreadyRunning as e:
            print(e, file=sys.stderr)
            return 1
        store = Store(cfg.state_dir / "dvtsync.db")
        rep = asyncio.run(Syncer(cfg, store).run_once())
        print(rep)
        return 1 if rep.failed else 0

    store = Store(cfg.state_dir / "dvtsync.db")
    if args.cmd == "status":
        print("files:", store.summary() or "none")
        print("last seen:   ", _fmt(store.get_kv("last_seen")))
        print("last sync ok:", _fmt(store.get_kv("last_sync_ok")))
        print("last error:  ", store.get_kv("last_error") or "-")
    elif args.cmd == "list":
        for r in store.all():
            print(f"{r['status']:<10} {r['modified'] or '-':<20} {r['length_bytes']:>10} "
                  f"try={r['attempts']} {r['remote_path']}  {r['last_error'] or ''}"
                  + (f"  bookmarks_ms={r['bookmarks_ms']}" if r['bookmarks_ms'] not in (None, '[]') else ""))
    elif args.cmd == "backfill":
        print(f"updated {Syncer(cfg, store).backfill()} file(s)")
    elif args.cmd == "retry":
        print(f"reset {store.retry_failed()} file(s)")
    return 0


async def _run_service(cfg) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run(cfg, stop)
    except AlreadyRunning as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
