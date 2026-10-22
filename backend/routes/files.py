from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from backend.dependencies import AWS, User
from backend.models import (
    ConfirmUpload,
    DownloadResult,
    FilePage,
    FileView,
    HashCheck,
    HashResult,
    StorageStats,
    UploadRequest,
    UploadResult,
)

router = APIRouter(prefix="/api", tags=["files"])
VERIFIED = {"QUEUED", "PROCESSING", "READY", "FAILED"}


def view(ref, master):
    return FileView(
        file_id=ref["file_id"],
        filename=ref["filename"],
        sha256=master["sha256"],
        size_bytes=master["size_bytes"],
        content_type=master["content_type"],
        status=master["status"],
        created_at=ref["created_at"],
    )


def owned_file(services, user_id, file_id):
    ref = services.database.get_reference(user_id, file_id)
    if ref is None:
        raise HTTPException(404, "File not found")
    master = services.database.get_hash(user_id, ref["file_hash"].split(":", 1)[1])
    if master is None:
        raise HTTPException(503, "File metadata unavailable")
    return ref, master


@router.post("/files/check-hash", response_model=HashResult)
def check_hash(body: HashCheck, user_id: User, services: AWS):
    master = services.database.get_hash(user_id, body.sha256)
    if master is None or master["status"] not in VERIFIED:
        return HashResult(duplicate=False)
    ref = services.database.link(user_id, body.filename, master)
    return HashResult(duplicate=True, savings_bytes=master["size_bytes"], file=view(ref, master))


@router.post("/files/initiate-upload", response_model=UploadResult)
def initiate_upload(body: UploadRequest, request: Request, user_id: User, services: AWS):
    if body.size_bytes > request.app.state.settings.max_upload_bytes:
        raise HTTPException(413, "File exceeds configured upload limit")
    try:
        ref, master = services.database.reserve(user_id, body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if master["status"] in VERIFIED:
        return UploadResult(duplicate=True, file=view(ref, master))
    url, headers = services.storage.presign_upload(master)
    return UploadResult(
        duplicate=False,
        file=view(ref, master),
        upload_url=url,
        upload_headers=headers,
        expires_in=300,
    )


@router.post("/files/confirm-upload", response_model=FileView)
def confirm_upload(body: ConfirmUpload, user_id: User, services: AWS):
    ref, master = owned_file(services, user_id, body.file_id)
    if master["status"] != "PENDING_UPLOAD":
        return view(ref, master)
    if not services.storage.verify_upload(master):
        raise HTTPException(409, "Uploaded object is missing or its size, checksum or type differs")
    # Send first: a failed send leaves the upload retryable instead of stranding it as QUEUED.
    services.queue.enqueue(master)
    services.database.mark_queued(master)
    master = services.database.get_hash(user_id, master["sha256"])
    return view(ref, master)


@router.get("/files", response_model=FilePage)
def list_files(
    user_id: User,
    services: AWS,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(pattern=r"^[a-f0-9]{32}$")] = None,
):
    refs, next_cursor = services.database.list_references(user_id, limit, cursor)
    masters = {}
    files = []
    for ref in refs:
        key = ref["file_hash"]
        if key not in masters:
            masters[key] = owned_file(services, user_id, ref["file_id"])[1]
        files.append(view(ref, masters[key]))
    return FilePage(files=files, next_cursor=next_cursor)


@router.get("/files/{file_id}/download", response_model=DownloadResult)
def download_file(
    file_id: str,
    user_id: User,
    services: AWS,
    variant: Literal["original", "processed"] = "original",
):
    _, master = owned_file(services, user_id, file_id)
    if master["status"] not in VERIFIED:
        raise HTTPException(409, "Upload has not been confirmed")
    if variant == "processed" and (
        master["status"] != "READY" or not master.get("processed_s3_key")
    ):
        raise HTTPException(409, "Processed file is not ready")
    return DownloadResult(download_url=services.storage.presign_download(master, variant))


@router.get("/stats", response_model=StorageStats, tags=["stats"])
def stats(user_id: User, services: AWS):
    cursor = None
    masters = {}
    logical = pending = total = 0
    while True:
        refs, cursor = services.database.list_references(user_id, cursor=cursor)
        for ref in refs:
            total += 1
            key = ref["file_hash"]
            if key not in masters:
                masters[key] = owned_file(services, user_id, ref["file_id"])[1]
            master = masters[key]
            if master["status"] in VERIFIED:
                logical += int(master["size_bytes"])
            else:
                pending += 1
        if cursor is None:
            break
    raw = sum(int(m["size_bytes"]) for m in masters.values() if m["status"] in VERIFIED)
    processed = sum(int(m.get("processed_size_bytes", 0)) for m in masters.values())
    saved = logical - raw
    return StorageStats(
        total_logical_files=total,
        pending_files=pending,
        logical_bytes=logical,
        unique_raw_bytes=raw,
        processed_bytes=processed,
        physical_bytes=raw + processed,
        deduplication_saved_bytes=saved,
        bandwidth_saved_percent=round(saved / logical * 100, 2) if logical else 0,
    )
