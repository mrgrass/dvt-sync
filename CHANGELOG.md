# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]
### Added
- GitHub Actions CI: ruff and tests on Python 3.11–3.13.
- pre-commit hooks: file checks, large-file guard, ruff, uv lock check.
- `CONTRIBUTING.md` with the git flow branching model.

## 0.1.0 (not yet tagged)
### Added
- Presence-triggered sync service for the Philips VoiceTracer DVT6110 (`dvtsync`).
- JSON sidecars and SQLite manifest for downstream pipeline stages.
- Protocol documentation, exploration tools, fake-device tests, example systemd unit.
