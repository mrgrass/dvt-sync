# Roadmap

1. **Complete the protocol.** Capture the official Philips app's traffic (mitmproxy or
   Wireshark on a hotspot) to learn the formats of `DeleteFile`, `SetSettings` and the
   `timeFromPVT` response. Test `GetMetaData` and larger downloads.
2. **Python sync service.** Done (`dvtsync/`): presence detection, download with size
   check, atomic writes, retries, SQLite manifest, tests against a fake device.
3. **Swift protocol library.** `URLSessionWebSocketTask` for the WebSocket, `NWBrowser` for
   Bonjour (`dvt.local`), `NSLocalNetworkUsageDescription` and `NSBonjourServices` in
   Info.plist, and an ATS exception for local `ws://` (`NSAllowsLocalNetworking`).
4. **iOS app.** Discovery, file list with status, download after a recording ends, upload queue
   to the pipeline, start/stop recording, time sync. Long background transfers are limited on
   iOS: transfer with the app open or use short background tasks.
5. **Pipeline integration.** The next stages (transcription, summary) consume the manifest, see
   [design.md](design.md).

## Open questions
- How to configure the recorder's Wi‑Fi without the official app.
- Which firmware versions other than 1.20 behave the same way.
- Whether the `buffersize` crash can be explained, which would allow faster downloads.
