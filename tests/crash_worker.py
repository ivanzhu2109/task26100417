"""崩溃测试工作进程：逐块上传并发布，每步确认后打印一行，供父进程 SIGKILL。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chunked_upload import UploadManager

TOTAL = 64 * 1024
CHUNK = 1024


def chunk_bytes(i):
    return bytes([(i * 7 + 13) % 256]) * CHUNK


def main():
    root = sys.argv[1]
    mgr = UploadManager(root)
    mgr.open_upload("u1", "f.bin", TOTAL)
    for i in range(TOTAL // CHUNK):
        mgr.submit_chunk("u1", i * CHUNK, chunk_bytes(i))
        print(f"chunk {i}", flush=True)
    mgr.publish("u1")
    print("publish", flush=True)
    # 继续空转，给父进程在发布完成后发送信号的时间
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
