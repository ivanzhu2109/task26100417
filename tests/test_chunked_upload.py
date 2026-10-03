"""分块续传与原子发布库的自动化测试。"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import (
    ChunkConflictError,
    InvalidChunkError,
    InvalidNameError,
    UploadAlreadyPublishedError,
    UploadExistsError,
    UploadIncompleteError,
    UploadManager,
    UploadNotFoundError,
)


class TempDirMixin(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def target_path(self, name):
        return os.path.join(self.root, "files", name)

    def read_target(self, name):
        with open(self.target_path(name), "rb") as f:
            return f.read()


class ReferenceModel:
    """独立的小文件参考模型：用 bytearray 记录已确认字节。"""

    def __init__(self, total):
        self.total = total
        self.data = bytearray(total)
        self.confirmed = [False] * total

    def submit(self, offset, chunk):
        end = offset + len(chunk)
        for i in range(offset, end):
            if self.confirmed[i] and self.data[i] != chunk[i - offset]:
                return False  # 冲突：整体拒绝
        for i in range(offset, end):
            self.data[i] = chunk[i - offset]
            self.confirmed[i] = True
        return True

    def intervals(self):
        out = []
        i = 0
        while i < self.total:
            if self.confirmed[i]:
                j = i
                while j < self.total and self.confirmed[j]:
                    j += 1
                out.append((i, j))
                i = j
            else:
                i += 1
        return out

    def confirmed_bytes(self):
        return sum(self.confirmed)


class ModelComparisonTest(TempDirMixin):
    """乱序、重复、跨区域、含冲突的随机操作与参考模型对比。"""

    def test_random_ops_match_model(self):
        rng = random.Random(20261004)
        total = 3000
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("u", "f.bin", total)
        model = ReferenceModel(total)
        content = bytearray(rng.randbytes(total))

        for step in range(400):
            offset = rng.randrange(0, total)
            length = rng.randrange(1, 200)
            length = min(length, total - offset)
            if rng.random() < 0.25:
                # 制造可能含冲突字节的数据
                chunk = bytearray(content[offset : offset + length])
                for _ in range(rng.randrange(1, 4)):
                    chunk[rng.randrange(len(chunk))] ^= 0xFF
                chunk = bytes(chunk)
            else:
                chunk = bytes(content[offset : offset + length])
            try:
                progress = mgr.submit_chunk("u", offset, chunk)
                accepted = True
            except ChunkConflictError:
                accepted = False
            self.assertEqual(accepted, model.submit(offset, chunk), f"step {step}")
            self.assertEqual(progress.intervals, model.intervals(), f"step {step}")
            self.assertEqual(progress.confirmed_bytes, model.confirmed_bytes())

        # 补齐剩余空洞后发布，内容须与参考模型逐字节一致
        pos = 0
        for start, end in model.intervals():
            if pos < start:
                fill = bytes(content[pos:start])
                mgr.submit_chunk("u", pos, fill)
                model.submit(pos, fill)
            pos = max(pos, end)
        if pos < total:
            fill = bytes(content[pos:total])
            mgr.submit_chunk("u", pos, fill)
            model.submit(pos, fill)
        progress = mgr.publish("u")
        self.assertTrue(progress.published)
        self.assertEqual(self.read_target("f.bin"), bytes(model.data))

    def test_conflicting_chunk_is_fully_rejected(self):
        """同时包含新字节和冲突旧字节的请求不得改变任何状态。"""
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("u", "f.bin", 16)
        mgr.submit_chunk("u", 0, b"AAAA")
        before = mgr.get_progress("u")
        # 偏移 2 起：[2,4) 与已确认区冲突，[4,8) 本是新字节
        with self.assertRaises(ChunkConflictError):
            mgr.submit_chunk("u", 2, b"XXBBBB")
        after = mgr.get_progress("u")
        self.assertEqual(before.intervals, after.intervals)
        self.assertEqual(before.confirmed_bytes, after.confirmed_bytes)
        # 冲突请求中的新字节部分也未生效：重新提交正确数据应被接受
        mgr.submit_chunk("u", 2, b"AABBBB")
        self.assertEqual(mgr.get_progress("u").intervals, [(0, 8)])


class RuleTest(TempDirMixin):
    def test_reopen_same_params_returns_original(self):
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("u", "f.bin", 10)
        mgr.submit_chunk("u", 0, b"01234")
        meta = mgr.open_upload("u", "f.bin", 10)  # 同参数重复开启
        self.assertEqual(meta.upload_id, "u")
        self.assertEqual(mgr.get_progress("u").intervals, [(0, 5)])  # 进度未重置
        with self.assertRaises(UploadExistsError):
            mgr.open_upload("u", "other.bin", 10)
        with self.assertRaises(UploadExistsError):
            mgr.open_upload("u", "f.bin", 20)

    def test_same_target_name_allowed_for_different_uploads(self):
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("u1", "f.bin", 4)
        mgr.open_upload("u2", "f.bin", 4)
        mgr.submit_chunk("u1", 0, b"1111")
        mgr.submit_chunk("u2", 0, b"2222")
        mgr.publish("u1")
        self.assertEqual(self.read_target("f.bin"), b"1111")
        mgr.publish("u2")
        self.assertEqual(self.read_target("f.bin"), b"2222")

    def test_chunk_validation(self):
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("u", "f.bin", 100)
        with self.assertRaises(InvalidChunkError):
            mgr.submit_chunk("u", -1, b"x")
        with self.assertRaises(InvalidChunkError):
            mgr.submit_chunk("u", 99, b"xx")  # 超出声明长度
        with self.assertRaises(InvalidChunkError):
            mgr.submit_chunk("u", 0, b"x" * (4 * 1024 * 1024 + 1))
        with self.assertRaises(UploadNotFoundError):
            mgr.submit_chunk("nope", 0, b"x")
        with self.assertRaises(UploadNotFoundError):
            mgr.get_progress("nope")
        with self.assertRaises(InvalidNameError):
            mgr.open_upload("bad", "../evil", 1)

    def test_holes_do_not_count_as_uploaded(self):
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("u", "f.bin", 64)
        mgr.submit_chunk("u", 32, b"\x00" * 8)  # 写入全零分块
        progress = mgr.get_progress("u")
        # 未写入的空洞即使读取结果为零也不能算已上传
        self.assertEqual(progress.intervals, [(32, 40)])
        self.assertEqual(progress.confirmed_bytes, 8)
        with self.assertRaises(UploadIncompleteError):
            mgr.publish("u")

    def test_publish_rules(self):
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("u", "f.bin", 8)
        with self.assertRaises(UploadIncompleteError):
            mgr.publish("u")
        mgr.submit_chunk("u", 0, b"12345678")
        mgr.publish("u")
        with self.assertRaises(UploadAlreadyPublishedError):
            mgr.submit_chunk("u", 0, b"12345678")
        # 重复发布返回原成功结论
        progress = mgr.publish("u")
        self.assertTrue(progress.published)
        self.assertEqual(self.read_target("f.bin"), b"12345678")

    def test_old_upload_republish_does_not_rollback(self):
        """同名文件连续发布后，旧上传的发布重试不得回滚目标。"""
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("u1", "f.bin", 4)
        mgr.open_upload("u2", "f.bin", 4)
        mgr.submit_chunk("u1", 0, b"OLD!")
        mgr.submit_chunk("u2", 0, b"NEW!")
        mgr.publish("u1")
        mgr.publish("u2")
        self.assertEqual(self.read_target("f.bin"), b"NEW!")
        progress = mgr.publish("u1")  # 旧上传重试发布
        self.assertTrue(progress.published)
        self.assertEqual(self.read_target("f.bin"), b"NEW!")

    def test_publish_replaces_existing_target_atomically(self):
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        os.makedirs(os.path.join(self.root, "files"), exist_ok=True)
        with open(self.target_path("f.bin"), "wb") as f:
            f.write(b"OLD-VERSION")
        mgr.open_upload("u", "f.bin", 11)
        mgr.submit_chunk("u", 0, b"NEW-VERSION")
        mgr.publish("u")
        self.assertEqual(self.read_target("f.bin"), b"NEW-VERSION")

    def test_zero_length_file(self):
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        mgr.open_upload("u", "empty.bin", 0)
        progress = mgr.publish("u")
        self.assertTrue(progress.published)
        self.assertEqual(self.read_target("empty.bin"), b"")

    def test_state_survives_reopen(self):
        mgr = UploadManager(self.root)
        mgr.open_upload("u", "f.bin", 8)
        mgr.submit_chunk("u", 0, b"0123")
        mgr.close()
        mgr2 = UploadManager(self.root)
        self.addCleanup(mgr2.close)
        progress = mgr2.get_progress("u")
        self.assertEqual(progress.intervals, [(0, 4)])
        mgr2.submit_chunk("u", 4, b"4567")
        mgr2.publish("u")
        self.assertEqual(self.read_target("f.bin"), b"01234567")


if __name__ == "__main__":
    unittest.main()
