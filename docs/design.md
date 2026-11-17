# Sync service design

## Contract with the pipeline (transcription, summary)
- The queue is the SQLite manifest `state/dvtsync.db`, table `files`. Downstream stages read
  rows with `status='downloaded'` (audio in `recordings/<local_name>`, plus a `.json` sidecar).
  They keep their own state in their own tables and never change this service's columns.
- A file appears in `recordings/` only when complete: it is written to `.part`, fsync'ed, then
  atomically renamed.
- Next to each audio file there is a `<file>.mp3.json` sidecar with the recorder metadata,
  recording time, serial number, settings and sha256.

## Robustness
- `buffersize` is fixed to `"1024"` (`"4096"` hung the device). One connection per operation.
- Downloads are postponed while the recorder is not in `STOP` (it is recording). The state is
  checked again before each file.
- The end of a transfer is detected by bytes received, never by the `"P"` heartbeat. A stall
  timeout aborts transfers that stop sending data.
- After any error the device is left alone for a while (`failure_cooldown`). Each file has its
  own exponential backoff and is then marked `failed` (re-armed with `dvtsync retry`). Files
  that grew after being downloaded are downloaded again.
- Complete files already on disk are adopted instead of downloaded again. Only one instance
  runs at a time (flock).

## To be verified on the device
- Received files are a multiple of 1024 bytes (> `length_bytes`). The extra bytes are kept and
  recorded (`bytes_received` vs `length_bytes`), not truncated.
- Untested: several downloads on one connection, `GetMetaData`, network loss mid-file.
