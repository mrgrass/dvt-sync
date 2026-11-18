# Contributing

## Branching model (git flow)
- `main`: released versions only. Every release is tagged (`vX.Y.Z`).
- `develop`: integration branch. **Open pull requests against `develop`.**
- `feature/<name>`: branched from `develop`, merged back into `develop`.
- `release/X.Y.Z`: branched from `develop` to prepare a release (version bump, changelog),
  merged into `main` (tagged) and back into `develop`.
- `hotfix/<name>`: branched from `main` for urgent fixes, merged into `main` (tagged) and
  back into `develop`.

## Setup
```sh
uv sync
uv run pre-commit install
```

## Before opening a pull request
- `uv run pytest` and `uv run ruff check .` pass (CI runs both on Python 3.11–3.13).
- Protocol changes: say what was confirmed on a real device and what was not
  (see [docs/protocol.md](docs/protocol.md)). Never send `DownloadFile` with a `buffersize`
  other than `"1024"`.
- Update [CHANGELOG.md](CHANGELOG.md) under "Unreleased".
