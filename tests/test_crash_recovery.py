"""强制终止后的恢复测试：成功分块不丢失、目标只能是完整版本。"""

import os
import select
import signal
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import UploadManager, UploadNotFoundError

TOTAL = 64 * 1024
CHUNK = 1024
OLD_CONTENT = b"OLD-VERSION!" * 100
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_worker.py")


def chunk_bytes(i):
    return bytes([(i * 7 + 13) % 256]) * CHUNK


def full_content():
    return b"".join(chunk_bytes(i) for i in range(TOTAL // CHUNK))


class PublishRecoveryTest(unittest.TestCase):
    """确定性覆盖发布中断的两个恢复分支。"""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _prepare_intent(self, rename_done):
        mgr = UploadManager(self.root)
        mgr.open_upload("u1", "f.bin", 4)
        mgr.submit_chunk("u1", 0, b"NEW!")
        mgr._store.set_publish_intent("u1", True)
        if rename_done:
            os.replace(
                os.path.join(self.root, "uploads", "u1.part"),
                os.path.join(self.root, "files", "f.bin"),
            )
        mgr.close()  # 模拟崩溃：意图已落库，发布结论未落库

    def test_crash_before_rename_redoes_publish(self):
        self._prepare_intent(rename_done=False)
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        progress = mgr.get_progress("u1")
        self.assertTrue(progress.published)
        with open(os.path.join(self.root, "files", "f.bin"), "rb") as f:
            self.assertEqual(f.read(), b"NEW!")

    def test_crash_after_rename_only_marks_published(self):
        self._prepare_intent(rename_done=True)
        mgr = UploadManager(self.root)
        self.addCleanup(mgr.close)
        progress = mgr.get_progress("u1")
        self.assertTrue(progress.published)
        with open(os.path.join(self.root, "files", "f.bin"), "rb") as f:
            self.assertEqual(f.read(), b"NEW!")


class CrashRecoveryTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        os.makedirs(os.path.join(self.root, "files"))
        with open(os.path.join(self.root, "files", "f.bin"), "wb") as f:
            f.write(OLD_CONTENT)

    def _run_killed_worker(self, kill_after_lines):
        proc = subprocess.Popen(
            [sys.executable, WORKER, self.root],
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
        )
        lines = []
        for _ in range(kill_after_lines):
            ready, _, _ = select.select([proc.stdout], [], [], 10.0)
            if not ready:
                break  # 工作进程已转入空转，不再产生新行
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line.strip())
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        rest = proc.stdout.read()
        lines.extend(l.strip() for l in rest.splitlines() if l.strip())
        proc.stdout.close()
        proc.stdin.close()
        return lines

    def _verify_after_crash(self, lines, trial):
        acked = [int(l.split()[1]) for l in lines if l.startswith("chunk ")]
        publish_acked = "publish" in lines

        target = os.path.join(self.root, "files", "f.bin")
        staging = os.path.join(self.root, "uploads", "u1.part")

        mgr = UploadManager(self.root)  # 重新打开即自动恢复
        self.addCleanup(mgr.close)
        try:
            progress = mgr.get_progress("u1")
        except UploadNotFoundError:
            # 开启上传本身未返回，允许完全未生效
            self.assertEqual(acked, [], f"trial {trial}")
            with open(target, "rb") as f:
                self.assertEqual(f.read(), OLD_CONTENT, f"trial {trial}")
            return

        # 已确认区间必须是 1KiB 对齐的前缀，且覆盖所有已确认分块
        self.assertEqual(len(progress.intervals), 1, f"trial {trial}")
        start, end = progress.intervals[0]
        self.assertEqual(start, 0)
        self.assertEqual(end % CHUNK, 0)
        if acked:
            self.assertGreaterEqual(end, (max(acked) + 1) * CHUNK)
        self.assertEqual(progress.confirmed_bytes, end)

        # 恢复出的进度与真实字节一致
        if os.path.exists(staging):
            with open(staging, "rb") as f:
                data = f.read(end)
            expected = b"".join(chunk_bytes(i) for i in range(end // CHUNK))
            self.assertEqual(data, expected, f"trial {trial}")

        # 目标只能是完整旧版本或完整新版本，绝不消失、绝不混合
        self.assertTrue(os.path.exists(target), f"trial {trial}")
        with open(target, "rb") as f:
            content = f.read()
        self.assertIn(content, (OLD_CONTENT, full_content()), f"trial {trial}")

        if publish_acked:
            # 已返回成功的发布在恢复后必须仍然有效
            self.assertTrue(progress.published, f"trial {trial}")
            self.assertEqual(content, full_content(), f"trial {trial}")
        if progress.published:
            self.assertEqual(content, full_content(), f"trial {trial}")

        # 恢复后可继续调用：补齐剩余分块并发布
        if not progress.published:
            for i in range(end // CHUNK, TOTAL // CHUNK):
                mgr.submit_chunk("u1", i * CHUNK, chunk_bytes(i))
            mgr.publish("u1")
            with open(target, "rb") as f:
                self.assertEqual(f.read(), full_content(), f"trial {trial}")

        # 再次重新打开，状态保持一致
        mgr.close()
        mgr2 = UploadManager(self.root)
        self.addCleanup(mgr2.close)
        self.assertTrue(mgr2.get_progress("u1").published)

    def test_crash_at_various_points(self):
        # 0..64 覆盖分块提交各阶段；65 覆盖发布完成后
        for kill_after in [0, 1, 2, 17, 33, 63, 64, 65, 66, 100]:
            with self.subTest(kill_after=kill_after):
                lines = self._run_killed_worker(kill_after)
                self._verify_after_crash(lines, kill_after)
                self.tearDown()
                self.setUp()


if __name__ == "__main__":
    unittest.main()
