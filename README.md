# dvt-sync

[![CI](https://github.com/mrgrass/dvt-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/mrgrass/dvt-sync/actions/workflows/ci.yml)

Automatic Wi‑Fi sync for the **Philips VoiceTracer DVT6110** voice recorder.

A small service (designed for a Raspberry Pi or any always-on Linux box) watches the network for
the recorder. When it appears it downloads every new recording into `recordings/`. Next to each
file it writes a `<file>.mp3.json` sidecar with the recorder metadata, recording time, serial
number, settings and sha256. It also keeps a SQLite manifest that downstream stages
(transcription, summarisation) can use as a queue.

**It never deletes anything from the recorder.**

> [!WARNING]
> The protocol was reverse-engineered and is not documented by Philips. The recorder's WebSocket
> has **no authentication**: use it only on a trusted network. See
> [docs/protocol.md](docs/protocol.md) for what is confirmed and what is not.

## Requirements
- Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/)
- A DVT6110 already connected to your Wi‑Fi (set up with the official app) and reachable as
  `dvt.local` (mDNS: Avahi on Linux)

## Usage
```sh
uv sync
cp config.example.toml config.toml         # then edit it if needed
uv run dvtsync -c config.toml once         # one sync cycle now
uv run dvtsync -c config.toml run          # long-running service
uv run dvtsync -c config.toml status       # manifest summary
uv run dvtsync -c config.toml list         # every known file
uv run dvtsync -c config.toml retry        # re-arm permanently failed files
uv run dvtsync -c config.toml backfill     # re-read bookmarks into sidecars/manifest
```

## Running as a systemd service
Edit the paths and user in [`deploy/dvt-sync.service`](deploy/dvt-sync.service), then:
```sh
sudo cp deploy/dvt-sync.service /etc/systemd/system/
sudo systemctl enable --now dvt-sync
journalctl -fu dvt-sync
```

## Development
```sh
uv sync                     # includes the dev tools (pytest, ruff, pre-commit)
uv run pre-commit install   # run lint and file checks on every commit
uv run pytest               # tests run against a fake recorder (tests/fake_device.py), no hardware needed
uv run ruff check .         # lint
```
CI (GitHub Actions) runs ruff and the tests on Python 3.11–3.13 for pushes to `main` and `develop` and for every pull request.

## Repository layout
| Path | Contents |
|---|---|
| `dvtsync/` | the package: WebSocket client, presence detection, sync, SQLite manifest, CLI |
| `tests/` | tests and a fake DVT6110 WebSocket server |
| `tools/` | exploration scripts used to reverse-engineer the protocol (probe, test download, firmware unpacker) |
| `deploy/` | systemd unit |
| `docs/` | documentation |

## Documentation
- [docs/protocol.md](docs/protocol.md): the DVT6110 Wi‑Fi protocol, firmware analysis, device verification
- [docs/design.md](docs/design.md): contract with the pipeline, robustness choices, open points
- [docs/roadmap.md](docs/roadmap.md): next steps and open questions
- [CONTRIBUTING.md](CONTRIBUTING.md): branching model (git flow) and development workflow

## Acknowledgements
The protocol was first described by
[Zontex/phillips-dvt-voicetracer-api](https://github.com/Zontex/phillips-dvt-voicetracer-api).

## Disclaimer
This is an independent project, not affiliated with or endorsed by Philips or Speech Processing
Solutions GmbH. "Philips" and "VoiceTracer" are trademarks of their respective owners. No
firmware or other proprietary material is included in this repository. Use at your own risk: an
unsupported command can hang the recorder until it is power-cycled (see
[docs/protocol.md](docs/protocol.md#safety-first)).

## License
[MIT](LICENSE)
