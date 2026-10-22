from typing import Literal

from pydantic import BaseModel, Field, field_validator

FileStatus = Literal["PENDING_UPLOAD", "QUEUED", "PROCESSING", "READY", "FAILED"]


class HashCheck(BaseModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    filename: str = Field(min_length=1, max_length=255)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in value):
            raise ValueError("Provide a filename without paths or control characters")
        return value


class UploadRequest(HashCheck):
    size_bytes: int = Field(gt=0, strict=True)
    content_type: Literal["image/png", "image/jpeg", "application/pdf"]


class ConfirmUpload(BaseModel):
    file_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class FileView(BaseModel):
    file_id: str
    filename: str
    sha256: str
    size_bytes: int
    content_type: str
    status: FileStatus
    created_at: str


class HashResult(BaseModel):
    duplicate: bool
    savings_bytes: int = 0
    file: FileView | None = None


class UploadResult(BaseModel):
    duplicate: bool
    file: FileView
    upload_url: str | None = None
    upload_headers: dict[str, str] = Field(default_factory=dict)
    expires_in: int | None = None


class FilePage(BaseModel):
    files: list[FileView]
    next_cursor: str | None = None


class DownloadResult(BaseModel):
    download_url: str
    expires_in: int = 60


class StorageStats(BaseModel):
    total_logical_files: int
    pending_files: int
    logical_bytes: int
    unique_raw_bytes: int
    processed_bytes: int
    physical_bytes: int
    deduplication_saved_bytes: int
    bandwidth_saved_percent: float
