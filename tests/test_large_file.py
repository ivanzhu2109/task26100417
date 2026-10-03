"""2 GiB 文件完整传输与内存占用验证。"""

import os
import resource
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import MAX_CHUNK_SIZE, UploadManager

TOTAL = 2 * 1024 * 1024 * 1024  # 2 GiB
CHUNK = MAX_CHUNK_SIZE  # 4 MiB
N_CHUNKS = TOTAL // CHUNK
MEMORY_LIMIT = 64 * 1024 * 1024  # 组件额外内存峰值上限 64 MiB


def chunk_bytes(i):
    return i.to_bytes(4, "little") * (CHUNK // 4)


def peak_rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # macOS: 字节


class LargeFileTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_2gib_full_transfer_memory_bounded(self):
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("big", "big.bin", TOTAL)
        # 先传偶数块，再传奇数块；重复提交已确认块验证续传无需重传
        mgr.submit_chunk("big", 0, chunk_bytes(0))
        mgr.submit_chunk("big", 0, chunk_bytes(0))
        baseline = peak_rss()
        for i in range(2, N_CHUNKS, 2):
            mgr.submit_chunk("big", i * CHUNK, chunk_bytes(i))
        for i in range(1, N_CHUNKS, 2):
            mgr.submit_chunk("big", i * CHUNK, chunk_bytes(i))
        extra = peak_rss() - baseline
        self.assertLess(
            extra,
            MEMORY_LIMIT,
            f"component extra memory {extra / 1024 / 1024:.1f} MiB >= 64 MiB",
        )
        progress = mgr.get_progress("big")
        self.assertEqual(progress.intervals, [(0, TOTAL)])
        self.assertEqual(progress.confirmed_bytes, TOTAL)
        mgr.publish("big")
        self.assertTrue(mgr.get_progress("big").published)
        # 逐字节校验发布内容（流式读取）
        target = os.path.join(self.root, "files", "big.bin")
        self.assertEqual(os.path.getsize(target), TOTAL)
        with open(target, "rb") as f:
            for i in range(N_CHUNKS):
                data = f.read(CHUNK)
                self.assertEqual(data, chunk_bytes(i), f"chunk {i} mismatch")
            self.assertEqual(f.read(), b"")


if __name__ == "__main__":
    unittest.main()
