"""Exception types raised by the chunked upload library."""


class UploadError(Exception):
    """Base class for all errors raised by this library."""


class UploadNotFoundError(UploadError):
    """The given upload id does not exist in this store."""


class UploadExistsError(UploadError):
    """The upload id exists but was opened with different parameters."""


class InvalidNameError(UploadError):
    """The target file name is not a plain safe file name."""


class InvalidChunkError(UploadError):
    """The chunk offset/length is outside the declared file bounds."""


class ChunkTooLargeError(InvalidChunkError):
    """A single chunk exceeds the maximum allowed chunk size."""


class ChunkConflictError(UploadError):
    """The chunk contradicts bytes that were already confirmed.

    Nothing from the rejected submission is applied.
    """


class UploadClosedError(UploadError):
    """The upload is already published and no longer accepts chunks."""


class IncompleteUploadError(UploadError):
    """Publish was requested before every declared byte was confirmed."""
