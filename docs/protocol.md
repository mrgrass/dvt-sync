# Philips VoiceTracer DVT6110 Wi‑Fi protocol

Reverse-engineered notes on the DVT6110 Wi‑Fi interface. They come from static analysis of the
firmware, from a third-party project, and from tests on a real device. Each statement says
whether it was **confirmed on the device** or is **unverified**.

Credits: the protocol was first described by
[Zontex/phillips-dvt-voicetracer-api](https://github.com/Zontex/phillips-dvt-voicetracer-api)
(MIT, 2023, Python, `main.py`, ~250 lines). This project shares no code with it.

## Safety first

- **Never use a `buffersize` other than `"1024"`** in `DownloadFile`. A request with `"4096"`
  hung the recorder until it was power-cycled (details below).
- **Do not modify or flash the recorder firmware.** It is not needed and may brick the device.
- **No authentication, no TLS.** Anyone on the same network can download, delete, or start
  recordings. Use only a trusted network (home LAN or a phone hotspot) and never expose port 81
  outside the LAN.
- Do not delete files from the recorder until the next stage has confirmed it received them.
  This project never deletes.

## Firmware analysis (static, not tested on the device)

- Analysed package: `DVT2019_V1.20.UPG` (version 1.20, 2021-09-09). `tools/extract_upg.py`
  unpacks it into three images (DVT211, DVT411, DVT611) with their individual files. The
  firmware is Philips property and is not included in this repository.
- SoC "ZVMP500" (Zaram Technology) with NAND. The main firmware (`SYSSTART.BIN`, duplicated as
  `SDCSTART.BIN`) is not encrypted (entropy ~5.8) but targets an unknown architecture: no
  recognisable ARM code, no symbols. The checksum in the image header is not decoded: it is not
  CRC32 and not a simple sum. No cryptographic signature was found, but one cannot be ruled out.
- Only the DVT6110 image contains `FWESP82X.BIN`: the ESP8266 firmware (Arduino core 2.2.1,
  PlatformIO) that handles Wi‑Fi. The API used by the official Philips app lives there.
- Conclusion: modifying the main firmware is risky and unnecessary. The existing Wi‑Fi API
  is enough.

## Protocol

### Discovery and transport
- mDNS/Bonjour host name `dvt.local`, service `_http._tcp`. **Confirmed.**
- Port 80: HTTP, service page only (404 on `/`). **Confirmed.**
- Port 81: plain WebSocket `ws://<host>:81/` (arduino-WebSocketsServer library). No
  authentication: a direct connection works and returns no 401. **Confirmed.** The `401 / Basic`
  strings in the firmware are library boilerplate.
- Network: the recorder joins a Wi‑Fi network as a client. The SSID and password are probably
  set by the official Philips app. How to configure them without that app is unknown.

### Session sequence (confirmed)
On connect the device sends, as text frames:
1. `"42"`
2. `"timeFromPVT"`: the recorder asks the client for the time. The response format is
   unknown. Not answering it caused no problems.
3. `"P"` heartbeat about once per second, **for the whole connection**: also between commands
   and in the middle of downloads.

### Commands
Plain-text commands, JSON responses.

| Command | Response | Status |
|---|---|---|
| `GetDeviceState` | `{"Device State":"STOP"}` | confirmed |
| `GetSerialNumber` | `{"Serial":"DVT6110XXXXXXXv1.2"}` | confirmed |
| `GetSettings` | object with the settings keys below | confirmed |
| `GetFileList` | **one JSON message per file** (not a single array), `{"Recordings":[{…}]}` | confirmed |
| `GetMetaData` | ? | untested |
| `StartRec` / `StopRec` | ? | untested |

Commands present in the firmware but with unknown message formats (they need a traffic capture
of the official app): `DeleteFile`, `SetSettings`, `SpeedTest`, `CreateNewFile`, `SetBookMark`,
`CurrentTime`. The download flow also references `Cancel`, `UnitDone` and
`SelectedFileSizeInByte`.

### Download (confirmed)
```json
{"DownloadFile": {"path": "/C/VOICE/A/<name>.mp3", "buffersize": "1024"}}
```
- Folders: `/C/VOICE/A/` to `/C/VOICE/D/`.
- Data arrives as consecutive binary frames.
- **The end of the transfer must be detected by counting bytes** (received ≥ `length_bytes`).
  Do not use `"P"`, because it is also the periodic heartbeat.
- The received size is slightly larger than the `length_bytes` reported by `GetFileList`
  (+223 and +720 bytes in two tests of 246 KB and 1.2 MB), probably an ID3 tag not counted in
  the metadata. The files are always a multiple of 1024 bytes. The downloaded files are valid
  MP3s, with bitrate and duration matching the reported `recordingformats`.
- **`buffersize` must be exactly `"1024"`.** A request with `"4096"` left the device unreachable
  from the network: no ping, no port 80, no port 81. It needed a physical power cycle. Possibly a
  buffer overflow or allocation failure on the ESP8266. Do not try other values without
  understanding the cause.

### JSON keys found in firmware strings
- File metadata: `filename, scene, recordingformats, modified_year/month/day/hour/minute/second, length, length_bytes`
- State: `RecordState` (`Recording`, `Started`, `Stopped`), `Device State`, `FileCounter`,
  `numberOfFiles`, `AudioLevelLeft/Right`, `AudioPosition`, `Serial`,
  `CurrentTime` (Year, Month, Hour, Minute, Second), `Recordings`
- Settings: `folder, recordingformat, micsensitivity, activemic, noisecut, windfilter, limiter`
- Values: folders `FOLDER_A..D`; scenes `SPEECH_RECOGNITION, INTERVIEW, LECTURE, CUSTOM, MEETING, NOTE, VIDEO`;
  formats `MP3_8/32/64/96/128/192/256/320`, `PCM_22/44/48/96`; microphone
  `MIC_VERYLOW..MIC_VERYHIGH`, `OMNI_DIRECTIONAL/UNI_DIRECTIONAL`; `NOISECUT_ON/OFF`,
  `WINDFILTER_ON/OFF`, `LIMITER_OFF/SLOW/MID/FAST`

## Device verification log

Tested on 2026-11-04 on a home LAN with `tools/probe.py` and `tools/download_test.py`.
Everything marked "confirmed" above comes from this session.

Not yet tested: `GetMetaData`, `DeleteFile`, `SetSettings`, `StartRec`/`StopRec`, a response
to `timeFromPVT`, several downloads on a single connection, and a connection dropped in the
middle of a download.

## Tools
- `tools/extract_upg.py`: unpacks a firmware package:
  `python3 tools/extract_upg.py DVT2019_V1.20.UPG out/`
- `tools/probe.py`: logs raw traffic and sends the read-only commands.
- `tools/download_test.py`: downloads one file and checks its size and MP3 header.
- `tools/download_all.py`: standalone script that downloads every file (it predates the service).
