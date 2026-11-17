import hashlib
import json

import pytest
from fake_device import FakeDevice

from dvtsync.client import DeviceError
from dvtsync.config import Config
from dvtsync.presence import PresenceTracker
from dvtsync.store import Store
from dvtsync.sync import Syncer

A = "/C/VOICE/A/261104_100454_00.mp3"
B = "/C/VOICE/A/261103_154426_00.mp3"


def blob(n, seed):
    return bytes((seed + i) % 251 for i in range(n))


@pytest.fixture
async def dev():
    d = await FakeDevice({A: blob(5000, 1), B: blob(1024, 2)}).start()
    yield d
    await d.stop()


@pytest.fixture
def cfg(tmp_path, dev):
    return Config(host="127.0.0.1", port=dev.port, recordings_dir=tmp_path / "rec",
                  state_dir=tmp_path / "state", handshake_wait=0.15, command_timeout=2,
                  list_quiet=0.3, stall_timeout=1, inter_file_pause=0, min_free_bytes=0,
                  backoff_base=0, max_attempts=3)


@pytest.fixture
def store(cfg):
    s = Store(cfg.state_dir / "db.sqlite")
    yield s
    s.close()


async def test_downloads_files_and_sidecars(cfg, store, dev):
    rep = await Syncer(cfg, store).run_once()
    assert sorted(rep.downloaded) == sorted(["261104_100454_00.mp3", "261103_154426_00.mp3"])
    f = cfg.recordings_dir / "261104_100454_00.mp3"
    assert f.read_bytes()[:5000] == dev.files[A]           # content intact
    side = json.loads((cfg.recordings_dir / (f.name + ".json")).read_text())
    assert side["metadata"]["scene"] == "MEETING"
    assert side["recorded_at"] == "2026-11-04T10:04:54"
    assert side["device"]["serial"] == "FAKE0001"
    assert side["length_bytes"] == 5000 and side["bytes_received"] == 5120  # padded to 1024
    assert side["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert store.summary() == {"downloaded": 2}
    assert not list(cfg.recordings_dir.glob("*.part"))
    assert all(r["buffersize"] == "1024" for r in dev.download_requests)


async def test_second_run_downloads_nothing(cfg, store, dev):
    s = Syncer(cfg, store)
    await s.run_once()
    n = len(dev.download_requests)
    rep = await s.run_once()
    assert rep.downloaded == [] and rep.new == 0
    assert len(dev.download_requests) == n


async def test_interrupted_download_is_retried(cfg, store, dev):
    s = Syncer(cfg, store)
    dev.break_after = 2048
    rep = await s.run_once()
    assert rep.failed and "261104_100454_00.mp3" not in rep.downloaded
    assert not list(cfg.recordings_dir.glob("*.part"))
    assert not (cfg.recordings_dir / "261104_100454_00.mp3").exists()   # no partial file exposed
    row = store.db.execute("SELECT status, attempts FROM files WHERE remote_path=?", (A,)).fetchone()
    assert row["status"] == "pending" and row["attempts"] == 1
    dev.break_after = None
    rep = await s.run_once()
    assert not rep.failed and rep.downloaded == ["261104_100454_00.mp3"]
    assert store.summary() == {"downloaded": 2}


async def test_gives_up_after_max_attempts_then_retry_resets(cfg, store, dev):
    s = Syncer(cfg, store)
    dev.break_after = 0
    for _ in range(3):
        await s.run_once()
    assert store.summary().get("failed", 0) >= 1
    dev.break_after = None
    await s.run_once()
    assert store.summary().get("failed", 0) >= 1          # no automatic retry
    store.retry_failed()
    await s.run_once()
    assert store.summary() == {"downloaded": 2}


async def test_busy_recorder_postpones_downloads(cfg, store, dev):
    dev.state = "RECORD"
    rep = await Syncer(cfg, store).run_once()
    assert rep.busy and rep.downloaded == [] and dev.download_requests == []
    assert store.summary() == {"pending": 2}
    dev.state = "STOP"
    rep = await Syncer(cfg, store).run_once()
    assert len(rep.downloaded) == 2


async def test_adopts_complete_existing_file(cfg, store, dev):
    cfg.recordings_dir.mkdir(parents=True)
    (cfg.recordings_dir / "261103_154426_00.mp3").write_bytes(dev.files[B])
    rep = await Syncer(cfg, store).run_once()
    assert rep.adopted == ["261103_154426_00.mp3"]
    assert dev.download_requests and all(r["path"] == A for r in dev.download_requests)
    assert (cfg.recordings_dir / "261103_154426_00.mp3.json").exists()


async def test_grown_file_is_downloaded_again(cfg, store, dev):
    s = Syncer(cfg, store)
    await s.run_once()
    dev.files[A] = blob(7000, 9)
    rep = await s.run_once()
    assert rep.downloaded == ["261104_100454_00.mp3"]
    assert (cfg.recordings_dir / "261104_100454_00.mp3").read_bytes()[:7000] == dev.files[A]


async def test_name_collision_across_folders(cfg, store, dev):
    dev.files["/C/VOICE/B/261104_100454_00.mp3"] = blob(1024, 7)
    rep = await Syncer(cfg, store).run_once()
    assert "B_261104_100454_00.mp3" in rep.downloaded and len(rep.downloaded) == 3


async def test_unreachable_device_raises(cfg, store):
    bad = Config(**{**cfg.__dict__, "port": 1})
    with pytest.raises(DeviceError):
        await Syncer(bad, store).run_once()


def test_presence_debounce():
    t = PresenceTracker(present_after=2, absent_after=3)
    assert t.update("1.2.3.4") is None
    assert t.update("1.2.3.4") is True
    assert t.update(None) is None and t.update("1.2.3.4") is None   # blip ignored
    assert [t.update(None) for _ in range(3)] == [None, None, False]
    assert not t.present


def _mp3_with_bookmarks(marks):
    import struct
    body = b"http://www.philips.com/dictation\0" + b"\0VOICE TRACER".ljust(34, b"\0")
    body = body.ljust(67, b"\0") + b"".join(struct.pack(">I", m << 8) for m in marks)
    body = body.ljust(314, b"\0")
    frame = b"PRIV" + struct.pack(">I", len(body)) + b"\0\0" + body
    n = len(frame)
    syncsafe = bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])
    return b"ID3\x03\x00\x00" + syncsafe + frame + b"\xff\xfb" * 600


def test_bookmarks_parsed(tmp_path):
    from dvtsync.id3 import read_bookmarks_ms
    f = tmp_path / "a.mp3"
    f.write_bytes(_mp3_with_bookmarks([35395, 90383]))
    assert read_bookmarks_ms(f, 100000) == [35395, 90383]
    assert read_bookmarks_ms(f, 5000) == []        # beyond duration: layout rejected
    f.write_bytes(_mp3_with_bookmarks([]))
    assert read_bookmarks_ms(f) == []


async def test_bookmarks_in_sidecar(cfg, store, dev):
    dev.files[A] = _mp3_with_bookmarks([35395, 90383])
    await Syncer(cfg, store).run_once()
    side = json.loads((cfg.recordings_dir / "261104_100454_00.mp3.json").read_text())
    assert side["bookmarks_ms"] == [35395, 90383]
    assert json.loads(store.db.execute("SELECT bookmarks_ms FROM files WHERE remote_path=?",
                                       (A,)).fetchone()[0]) == [35395, 90383]


async def test_stalled_download_times_out_despite_heartbeats(cfg, store, dev):
    dev.silent_download = True
    rep = await Syncer(cfg, store).run_once()
    assert rep.failed and rep.downloaded == []
    assert not list(cfg.recordings_dir.glob("*.part"))
    errs = [r[0] for r in store.db.execute("SELECT last_error FROM files WHERE last_error IS NOT NULL")]
    assert errs and all("stalled" in e for e in errs)
