"""2 GiB end-to-end transfer with memory-footprint verification."""

import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import MAX_CHUNK_SIZE
from tests.large_child import TOTAL, make_chunk

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "large_child.py")

#: Allowed RSS growth of the client process beyond the caller's chunk.
MEMORY_BUDGET = 64 * 1024 * 1024


class LargeTransferTest(unittest.TestCase):
    def test_two_gib_transfer_within_memory_budget(self):
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run(
                [sys.executable, CHILD, d],
                capture_output=True, text=True, timeout=1800,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            match = re.search(r"RSS_DELTA_BYTES=(\d+)", proc.stdout)
            self.assertIsNotNone(match, proc.stdout)
            delta = int(match.group(1))
            self.assertLess(
                delta, MEMORY_BUDGET,
                f"library memory growth {delta / 2**20:.1f} MiB exceeds budget",
            )
            # Stream-verify the published 2 GiB content block by block.
            target = os.path.join(d, "files", "big.bin")
            self.assertEqual(os.path.getsize(target), TOTAL)
            nchunks = TOTAL // MAX_CHUNK_SIZE
            with open(target, "rb") as fh:
                for i in range(nchunks):
                    block = fh.read(MAX_CHUNK_SIZE)
                    self.assertEqual(
                        block, make_chunk(i), f"content mismatch in chunk {i}"
                    )
                self.assertEqual(fh.read(), b"")
            print(f"\n2 GiB transfer OK, peak RSS growth: {delta / 2**20:.1f} MiB")


if __name__ == "__main__":
    unittest.main()
