"""Compare the store against an independent in-memory reference model.

The model tracks confirmed bytes with a per-byte bitmap on small files and
derives expected intervals from it.  Random workloads include out-of-order,
duplicate, partially overlapping chunks, chunks spanning several confirmed
regions, and chunks mixing new bytes with conflicting old bytes.
"""

import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import ChunkConflictError, UploadStore


class ReferenceModel:
    def __init__(self, length):
        self.length = length
        self.data = bytearray(length)
        self.confirmed = bytearray(length)  # 1 = confirmed byte

    def submit(self, offset, chunk):
        for i, b in enumerate(chunk):
            pos = offset + i
            if self.confirmed[pos] and self.data[pos] != b:
                return False  # conflict: reject everything
        for i, b in enumerate(chunk):
            pos = offset + i
            self.data[pos] = b
            self.confirmed[pos] = 1
        return True

    def intervals(self):
        out = []
        start = None
        for pos in range(self.length):
            if self.confirmed[pos] and start is None:
                start = pos
            elif not self.confirmed[pos] and start is not None:
                out.append((start, pos))
                start = None
        if start is not None:
            out.append((start, self.length))
        return out

    def confirmed_count(self):
        return sum(self.confirmed)


class ModelFuzzTest(unittest.TestCase):
    def run_workload(self, seed, length, rounds):
        rng = random.Random(seed)
        model = ReferenceModel(length)
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            store.open_upload("u", "f.bin", length)
            for _ in range(rounds):
                offset = rng.randrange(0, length)
                size = rng.randrange(1, min(64, length - offset) + 1)
                chunk = bytearray(rng.randbytes(size))
                # Half the time, reuse confirmed bytes for the overlapping
                # part (consistent resend); occasionally inject a conflict.
                mode = rng.random()
                if mode < 0.45:
                    for i in range(size):
                        pos = offset + i
                        if model.confirmed[pos]:
                            chunk[i] = model.data[pos]
                elif mode < 0.60:
                    for i in range(size):
                        pos = offset + i
                        if model.confirmed[pos]:
                            chunk[i] = model.data[pos] ^ 0xFF
                            break
                expected_ok = model.submit(offset, bytes(chunk))
                if expected_ok:
                    store.submit_chunk("u", offset, bytes(chunk))
                else:
                    before = store.progress("u")
                    with self.assertRaises(ChunkConflictError):
                        store.submit_chunk("u", offset, bytes(chunk))
                    after = store.progress("u")
                    self.assertEqual(before.intervals, after.intervals)
                    self.assertEqual(before.confirmed, after.confirmed)
                prog = store.progress("u")
                self.assertEqual(list(prog.intervals), model.intervals())
                self.assertEqual(prog.confirmed, model.confirmed_count())
            # Fill remaining holes with the model's own bytes and publish.
            for start, end in self._holes(model):
                chunk = bytes(model.data[start:end])
                store.submit_chunk("u", start, chunk)
                model.submit(start, chunk)
            prog = store.progress("u")
            self.assertTrue(prog.complete)
            result = store.publish("u")
            with open(result.path, "rb") as fh:
                self.assertEqual(fh.read(), bytes(model.data))
            store.close()

    @staticmethod
    def _holes(model):
        holes = []
        start = None
        for pos in range(model.length):
            if not model.confirmed[pos] and start is None:
                start = pos
            elif model.confirmed[pos] and start is not None:
                holes.append((start, pos))
                start = None
        if start is not None:
            holes.append((start, model.length))
        return holes

    def test_small_file_random_workloads(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                self.run_workload(seed=seed, length=200, rounds=120)

    def test_chunk_spanning_multiple_confirmed_regions(self):
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            store.open_upload("u", "f.bin", 30)
            store.submit_chunk("u", 0, b"aaaaaaaaaa")
            store.submit_chunk("u", 20, b"cccccccccc")
            # Spans the first region, the hole and the second region.
            store.submit_chunk("u", 5, b"aaaaabbbbbbbbbbccccc")
            prog = store.progress("u")
            self.assertEqual(list(prog.intervals), [(0, 30)])
            result = store.publish("u")
            with open(result.path, "rb") as fh:
                self.assertEqual(
                    fh.read(), b"aaaaaaaaaabbbbbbbbbbcccccccccc"
                )
            store.close()

    def test_mixed_new_and_conflicting_bytes_rejected_atomically(self):
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            store.open_upload("u", "f.bin", 20)
            store.submit_chunk("u", 0, b"0123456789")
            # New bytes in [10, 20) but a conflicting byte at offset 4.
            bad = b"0123X6789" + b"abcdefghij"
            with self.assertRaises(ChunkConflictError):
                store.submit_chunk("u", 0, bad)
            prog = store.progress("u")
            self.assertEqual(list(prog.intervals), [(0, 10)])
            self.assertEqual(prog.confirmed, 10)
            # The new bytes from the rejected request must not be confirmed.
            store.submit_chunk("u", 10, b"ABCDEFGHIJ")
            result = store.publish("u")
            with open(result.path, "rb") as fh:
                self.assertEqual(fh.read(), b"0123456789ABCDEFGHIJ")
            store.close()

    def test_zero_filled_hole_does_not_count_as_uploaded(self):
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            store.open_upload("u", "f.bin", 16)
            store.submit_chunk("u", 0, b"\x00" * 8)
            prog = store.progress("u")
            # The staging file reads as zeros in [8, 16) too, but the hole
            # must not count as confirmed.
            self.assertEqual(list(prog.intervals), [(0, 8)])
            self.assertEqual(prog.confirmed, 8)
            store.close()


if __name__ == "__main__":
    unittest.main()
