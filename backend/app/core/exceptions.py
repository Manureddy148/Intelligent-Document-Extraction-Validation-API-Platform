class AppError(Exception):
    """Base class for application errors that map to a controlled API response."""

    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class UnsupportedFileTypeError(AppError):
    code = "UNSUPPORTED_FILE_TYPE"
    status_code = 415


class EmptyFileError(AppError):
    code = "EMPTY_FILE"
    status_code = 400


class FileTooLargeError(AppError):
    code = "FILE_TOO_LARGE"
    status_code = 413


class CorruptedFileError(AppError):
    code = "CORRUPTED_FILE"
    status_code = 400


class PageLimitExceededError(AppError):
    code = "PAGE_LIMIT_EXCEEDED"
    status_code = 400


class DocumentNotFoundError(AppError):
    code = "DOCUMENT_NOT_FOUND"
    status_code = 404


class ExtractionFailedError(AppError):
    code = "EXTRACTION_FAILED"
    status_code = 422
