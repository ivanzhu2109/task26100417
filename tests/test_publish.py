"""Publish semantics: atomic replacement, retries, shared target names."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import (
    IncompleteUploadError,
    UploadClosedError,
    UploadExistsError,
    UploadStore,
)


class PublishSemanticsTest(unittest.TestCase):
    def test_same_name_sequential_publish_and_stale_retry(self):
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            # Upload 1 publishes version 1.
            store.open_upload("u1", "same.bin", 5)
            store.submit_chunk("u1", 0, b"ONE-1")
            r1 = store.publish("u1")
            target = os.path.join(d, "files", "same.bin")
            self.assertEqual(r1.path, target)
            with open(target, "rb") as fh:
                self.assertEqual(fh.read(), b"ONE-1")
            # Upload 2 reuses the same target name and replaces it.
            store.open_upload("u2", "same.bin", 5)
            store.submit_chunk("u2", 0, b"TWO-2")
            store.publish("u2")
            with open(target, "rb") as fh:
                self.assertEqual(fh.read(), b"TWO-2")
            # Retrying the old upload's publish must not roll back.
            r1_retry = store.publish("u1")
            self.assertEqual(r1_retry, r1)
            with open(target, "rb") as fh:
                self.assertEqual(fh.read(), b"TWO-2")
            # Reopen the store and retry again: still no rollback.
            store.close()
            store = UploadStore(d)
            store.publish("u1")
            with open(target, "rb") as fh:
                self.assertEqual(fh.read(), b"TWO-2")
            store.close()

    def test_target_keeps_old_version_until_publish(self):
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            store.open_upload("u1", "f.bin", 4)
            store.submit_chunk("u1", 0, b"OLD!")
            store.publish("u1")
            target = os.path.join(d, "files", "f.bin")
            # A second upload streams new bytes; target must stay OLD!.
            store.open_upload("u2", "f.bin", 4)
            store.submit_chunk("u2", 0, b"NE")
            store.submit_chunk("u2", 2, b"W!")
            with open(target, "rb") as fh:
                self.assertEqual(fh.read(), b"OLD!")
            store.publish("u2")
            with open(target, "rb") as fh:
                self.assertEqual(fh.read(), b"NEW!")
            store.close()

    def test_no_partial_file_visible_on_incomplete_upload(self):
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            store.open_upload("u", "f.bin", 8)
            store.submit_chunk("u", 0, b"1234")
            with self.assertRaises(IncompleteUploadError):
                store.publish("u")
            self.assertFalse(os.path.exists(os.path.join(d, "files", "f.bin")))
            store.close()

    def test_no_chunks_after_publish(self):
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            store.open_upload("u", "f.bin", 4)
            store.submit_chunk("u", 0, b"DATA")
            store.publish("u")
            with self.assertRaises(UploadClosedError):
                store.submit_chunk("u", 0, b"DATA")
            store.close()

    def test_open_upload_idempotent_and_parameter_check(self):
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            info1 = store.open_upload("u", "f.bin", 10)
            store.submit_chunk("u", 0, b"12345")
            info2 = store.open_upload("u", "f.bin", 10)  # same params
            self.assertEqual(info1, info2)
            # Progress must not be reset by re-opening.
            self.assertEqual(store.progress("u").confirmed, 5)
            with self.assertRaises(UploadExistsError):
                store.open_upload("u", "other.bin", 10)
            with self.assertRaises(UploadExistsError):
                store.open_upload("u", "f.bin", 11)
            store.close()

    def test_zero_length_upload(self):
        with tempfile.TemporaryDirectory() as d:
            store = UploadStore(d)
            store.open_upload("u", "empty.bin", 0)
            self.assertTrue(store.progress("u").complete)
            result = store.publish("u")
            self.assertEqual(os.path.getsize(result.path), 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
