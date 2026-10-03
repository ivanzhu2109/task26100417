"""Child-process helper for crash tests.

Runs one upload operation with a fault hook that hard-kills the process
(os._exit) at a chosen fault point, simulating a process crash.

Usage:
    python tests/crash_child.py <root> chunk <kill_point>
    python tests/crash_child.py <root> publish <kill_point>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import UploadStore

CHUNK_A = b"AAAA" * 1024          # 4 KiB at offset 0
CHUNK_B = b"BBBB" * 1024          # 4 KiB at offset 4096
LENGTH = 8192


def main():
    root, mode, kill_point = sys.argv[1], sys.argv[2], sys.argv[3]
    armed = {"on": False}

    def hook(point):
        if armed["on"] and point == kill_point:
            os._exit(42)  # simulate SIGKILL: no cleanup, no finally blocks

    store = UploadStore(root, fault_hook=hook)
    store.open_upload("u", "f.bin", LENGTH)
    if mode == "chunk":
        store.submit_chunk("u", 0, CHUNK_A)          # committed chunk
        armed["on"] = True
        store.submit_chunk("u", 4096, CHUNK_B)       # dies mid-commit
    elif mode == "publish":
        store.submit_chunk("u", 0, CHUNK_A)
        store.submit_chunk("u", 4096, CHUNK_B)
        armed["on"] = True
        store.publish("u")                            # dies mid-publish
    else:
        raise SystemExit(f"unknown mode {mode}")
    store.close()


if __name__ == "__main__":
    main()
