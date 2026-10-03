"""异常类型定义。"""


class UploadError(Exception):
    """所有上传相关错误的基类。"""


class UploadNotFoundError(UploadError):
    """上传标识不存在。"""


class UploadExistsError(UploadError):
    """同一上传标识以不同参数重复开启。"""


class InvalidChunkError(UploadError):
    """分块参数非法（大小、偏移越界等）。"""


class ChunkConflictError(UploadError):
    """分块与已确认区域存在不同字节，整次提交被拒绝。"""


class UploadIncompleteError(UploadError):
    """声明长度内仍有未确认字节，不允许发布。"""


class UploadAlreadyPublishedError(UploadError):
    """上传已发布，不再接受新分块。"""


class InvalidNameError(UploadError):
    """目标文件名非法。"""
