"""Crash-recovery tests: kill the process mid-operation, reopen, verify."""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import UploadStore
from tests.crash_child import CHUNK_A, CHUNK_B, LENGTH

CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_child.py")


def run_child(root, mode, kill_point):
    proc = subprocess.run(
        [sys.executable, CHILD, root, mode, kill_point],
        capture_output=True,
    )
    assert proc.returncode == 42, (
        f"child should have been killed at {kill_point}, "
        f"rc={proc.returncode} stderr={proc.stderr.decode()}"
    )


class ChunkCrashTest(unittest.TestCase):
    def test_crash_between_write_and_commit(self):
        for point in ("chunk.after_write", "chunk.before_commit"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as d:
                run_child(d, "chunk", point)
                store = UploadStore(d)  # reopen: auto-recovery
                prog = store.progress("u")
                # The committed chunk survives; the interrupted one is
                # either fully applied or fully absent (here: absent).
                self.assertEqual(list(prog.intervals), [(0, 4096)])
                self.assertEqual(prog.confirmed, 4096)
                self.assertFalse(prog.published)
                # Caller can simply retry the interrupted chunk.
                store.submit_chunk("u", 4096, CHUNK_B)
                prog = store.progress("u")
                self.assertEqual(list(prog.intervals), [(0, LENGTH)])
                result = store.publish("u")
                with open(result.path, "rb") as fh:
                    self.assertEqual(fh.read(), CHUNK_A + CHUNK_B)
                store.close()


class PublishCrashTest(unittest.TestCase):
    def test_crash_after_journal_before_replace(self):
        with tempfile.TemporaryDirectory() as d:
            run_child(d, "publish", "publish.after_journal")
            store = UploadStore(d)
            prog = store.progress("u")
            # The swap never happened: not published, target absent.
            self.assertFalse(prog.published)
            self.assertFalse(os.path.exists(os.path.join(d, "files", "f.bin")))
            # Retry completes the publish.
            result = store.publish("u")
            with open(result.path, "rb") as fh:
                self.assertEqual(fh.read(), CHUNK_A + CHUNK_B)
            self.assertTrue(store.progress("u").published)
            store.close()

    def test_crash_after_replace_before_commit(self):
        for point in ("publish.after_replace", "publish.before_commit"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as d:
                run_child(d, "publish", point)
                store = UploadStore(d)
                prog = store.progress("u")
                # The atomic swap already happened: recovery finalizes it.
                self.assertTrue(prog.published)
                target = os.path.join(d, "files", "f.bin")
                with open(target, "rb") as fh:
                    self.assertEqual(fh.read(), CHUNK_A + CHUNK_B)
                # Publish retry returns the original conclusion, no-op.
                result = store.publish("u")
                self.assertEqual(result.path, target)
                with open(target, "rb") as fh:
                    self.assertEqual(fh.read(), CHUNK_A + CHUNK_B)
                store.close()


if __name__ == "__main__":
    unittest.main()
