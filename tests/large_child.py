"""Child-process helper for the 2 GiB transfer / memory test.

Uploads a deterministic 2 GiB file in 4 MiB chunks and prints the peak
RSS growth of this process as ``RSS_DELTA_BYTES=<n>``.
"""

import os
import resource
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import MAX_CHUNK_SIZE, UploadStore

TOTAL = 2 * 1024 * 1024 * 1024  # 2 GiB


def make_chunk(index):
    return (index.to_bytes(8, "little")) * (MAX_CHUNK_SIZE // 8)


def peak_rss_bytes():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports KiB.
    return rss if sys.platform == "darwin" else rss * 1024


def main():
    root = sys.argv[1]
    store = UploadStore(root)
    store.open_upload("big", "big.bin", TOTAL)
    baseline = peak_rss_bytes()
    nchunks = TOTAL // MAX_CHUNK_SIZE
    for i in range(nchunks):
        store.submit_chunk("big", i * MAX_CHUNK_SIZE, make_chunk(i))
        if i % 64 == 0:
            prog = store.progress("big")
            assert prog.confirmed == (i + 1) * MAX_CHUNK_SIZE
    # Duplicate resubmission of an old chunk must be accepted and free.
    store.submit_chunk("big", 0, make_chunk(0))
    result = store.publish("big")
    assert os.path.getsize(result.path) == TOTAL
    store.close()
    delta = peak_rss_bytes() - baseline
    print(f"RSS_DELTA_BYTES={delta}", flush=True)


if __name__ == "__main__":
    main()
