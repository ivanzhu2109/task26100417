# chunked_upload：分块续传与原子发布库

纯 Python 库，由上层直接调用。文件内容保存在本地目录，上传状态保存在 SQLite。

## 用法

```python
from chunked_upload import UploadManager

mgr = UploadManager("/var/lib/uploads")   # 重新打开同一目录即自动恢复
mgr.open_upload("upload-123", "video.mp4", total=1_000_000_000)
mgr.submit_chunk("upload-123", offset=0, data=b"...")   # 单块 <= 4 MiB，可乱序/重复/重叠
progress = mgr.get_progress("upload-123")  # 精确已确认区间 + 是否已发布
mgr.publish("upload-123")                  # 全部字节确认后原子替换目标
mgr.close()
```

目标文件位于 `<root>/files/<name>`， staging 文件位于 `<root>/uploads/`，
状态库为 `<root>/state.db`。

## 语义

- `open_upload`：同一标识 + 相同参数重复开启返回原上传，不重置进度；参数不同抛
  `UploadExistsError`。不同上传可共用同一目标文件名。
- `submit_chunk`：与已确认区域重叠的字节全部相同才接受；存在任一不同字节则整次
  拒绝（`ChunkConflictError`），不改变任何已确认内容与进度。空洞不计入进度。
- `get_progress`：返回合并后的已确认字节区间、已确认字节数与发布状态。
- `publish`：仅当 `[0, total)` 全部确认才允许；通过 `os.replace` 原子切换，
  目标只能是完整旧版本或完整新版本。重复发布返回原成功结论，不回滚目标。

## 崩溃恢复

- 分块提交顺序：写数据并 `fsync` → SQLite 事务提交区间（WAL + `synchronous=FULL`）。
  已返回成功的分块重启后不丢失；未返回的提交要么完整生效要么完全未生效。
- 发布顺序：记录发布意图 → 原子 rename → 确认发布。重新打开时自动补完中断的
  发布（重做 rename 或仅补记结论），无需人工修复。

## 非功能指标

- 单文件支持 2 GiB（偏移为 64 位整数，staging 为稀疏文件）。
- 除调用方传入的分块外，组件额外内存峰值远小于 64 MiB（比对窗口 1 MiB）。
- 进度查询复杂度取决于区间数量而非文件字节数；区间在写入时即合并。

## 测试

```sh
python3 -m unittest discover -s tests
```

覆盖：随机乱序/重复/跨区域/含冲突操作与独立参考模型对比、SIGKILL 强制终止后的
恢复、发布中断两个恢复分支、同名文件连续发布后旧上传重试不回滚、2 GiB 完整传输
与内存占用验证。
