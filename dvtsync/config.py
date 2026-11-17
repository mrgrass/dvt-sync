"""Configuration: defaults < TOML file (--config). Unknown keys are rejected."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Config:
    # --- device ---
    host: str = "dvt.local"
    port: int = 81
    # --- storage (relative paths are resolved against the config file / cwd) ---
    recordings_dir: Path = Path("recordings")  # audio + <name>.json sidecars (pipeline inbox)
    state_dir: Path = Path("state")            # sqlite db + lock file
    # --- presence ---
    poll_interval: float = 15.0        # s between probes while the recorder is absent
    present_interval: float = 120.0    # s between presence re-checks while it is present
    connect_timeout: float = 4.0
    present_confirmations: int = 2     # consecutive good probes before "arrived"
    absent_confirmations: int = 3      # consecutive bad probes before "left"
    # --- sync ---
    rescan_interval: float = 300.0     # s between GetFileList runs while present and idle
    handshake_wait: float = 3.0        # s to let "42"/timeFromPVT/heartbeat pass (confirmed need)
    command_timeout: float = 10.0
    list_quiet: float = 3.0            # s of silence that ends a GetFileList burst
    stall_timeout: float = 30.0        # s without a data frame -> download failed
    inter_file_pause: float = 2.0      # s between files (device is fragile)
    max_attempts: int = 5
    min_free_bytes: int = 500_000_000  # refuse to download below this much free disk
    failure_cooldown: float = 120.0    # s to leave the device alone after any failure
    backoff_base: float = 60.0         # per-file retry delay = base * 2**(attempts-1), capped
    backoff_max: float = 3600.0
    # buffersize is NOT configurable: only "1024" is known-safe (4096 hung the device).

    def validate(self) -> Config:
        for name in ("poll_interval", "present_interval", "connect_timeout", "rescan_interval",
                     "command_timeout", "stall_timeout"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.present_confirmations < 1 or self.absent_confirmations < 1 or self.max_attempts < 1:
            raise ValueError("confirmations and max_attempts must be >= 1")
        return self


def load(path: str | Path | None) -> Config:
    if path is None:
        return Config().validate()
    path = Path(path)
    raw = tomllib.loads(path.read_text())
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"unknown config keys in {path}: {sorted(unknown)}")
    for key in ("recordings_dir", "state_dir"):
        if key in raw:
            p = Path(raw[key]).expanduser()
            raw[key] = p if p.is_absolute() else (path.parent / p)
    return Config(**raw).validate()
