# chunked-upload

Resumable chunked uploads with atomic publish, backed by a local
directory for file content and SQLite for upload state.  Plain library —
no services, no ports, no Docker.

## Usage

```python
from chunked_upload import UploadStore

store = UploadStore("/var/lib/uploads")      # reopens recover automatically

store.open_upload("upload-123", "big.bin", length=10_000_000)
store.submit_chunk("upload-123", 0, b"...")          # <= 4 MiB per chunk
store.submit_chunk("upload-123", 5_000_000, b"...")  # any order, retries OK

prog = store.progress("upload-123")
print(prog.intervals, prog.confirmed, prog.published)

if prog.complete:
    store.publish("upload-123")              # atomic swap of files/big.bin
```

## Guarantees

- **Idempotent open**: same upload id + same parameters reattaches to the
  original upload without resetting progress; different uploads may share
  one target file name.
- **Exactly-confirmed progress**: `progress()` reports precise confirmed
  byte intervals.  Holes never count, even if they read back as zeros.
- **Conflict-safe chunks**: chunks may arrive out of order, duplicated or
  partially overlapping.  If any byte contradicts an already confirmed
  region, the whole submission is rejected and nothing is applied.
- **Atomic publish**: the target file is replaced with `os.replace()` —
  readers only ever see the complete old version or the complete new one.
  Publish requires every declared byte to be confirmed; afterwards the
  upload rejects new chunks, and publish retries return the original
  result without touching the target (no rollback of newer content).
- **Crash recovery**: a process killed at any point leaves the store
  consistent.  Committed chunks and publishes survive reopening;
  interrupted operations take effect fully or not at all.  Publish is
  journaled in SQLite and reconciled on open.
- **Bounded memory**: besides the caller's chunk buffer (<= 4 MiB), the
  library uses a few MiB at most — verified at 2 GiB file size.

## Layout

```
<root>/state.db          upload metadata, confirmed intervals, publish journal
<root>/staging/<token>   per-upload staging file (confirmed bytes only)
<root>/files/<name>      published files
```

## Tests

```
python3 -m unittest discover -s tests -v
```

Covers reference-model fuzzing (out-of-order / duplicate / overlapping /
conflicting chunks), kill-at-any-point crash recovery for chunk commit
and publish, same-name sequential publish with stale retry, and a full
2 GiB transfer with a 64 MiB memory budget.
