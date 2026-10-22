import hashlib
import io
import json
import time

import pytest
from botocore.exceptions import ClientError
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from backend.services.database import encode
from backend.services.storage import checksum
from tests.conftest import confirmed, initiate
from worker.processor import InvalidFile, ProcessingTimeout, process, process_isolated
from worker.runner import Worker
from worker.state import WorkerState


def image_bytes(format="PNG", size=(600, 400)):
    output = io.BytesIO()
    Image.new("RGB", size, (45, 115, 150)).save(output, format)
    return output.getvalue()


def pdf_bytes():
    output = io.BytesIO()
    pdf = canvas.Canvas(output, invariant=1)
    pdf.drawString(80, 720, "A CloudVault document")
    pdf.save()
    return output.getvalue()


def queued_image(cloud):
    data = image_bytes()
    result = initiate(cloud, data, filename="photo.png", content_type="image/png").json()
    cloud.s3.put_object(
        Bucket=cloud.settings.raw_bucket,
        Key=f"alice/{hashlib.sha256(data).hexdigest()}",
        Body=data,
        ContentType="image/png",
        ChecksumAlgorithm="SHA256",
        ChecksumSHA256=checksum(hashlib.sha256(data).hexdigest()),
    )
    response = cloud.client.post(
        "/api/files/confirm-upload", json={"file_id": result["file"]["file_id"]}
    )
    assert response.status_code == 200
    return response.json()


def take_message(cloud):
    return cloud.app.state.services.queue.receive(wait_seconds=0)[0]


def master_for(cloud, file):
    return cloud.app.state.services.database.get_hash("alice", file["sha256"])


def reset_visibility(cloud, message):
    cloud.app.state.services.queue.defer(message["ReceiptHandle"], 0)


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_images_are_optimized_watermarked_and_thumbnailed(format, mime):
    result = process(image_bytes(format, (2400, 1600)), mime)
    main = Image.open(io.BytesIO(result.body))
    thumb = Image.open(io.BytesIO(result.thumbnail))
    assert main.format == "JPEG"
    assert max(main.size) == 2048
    assert max(thumb.size) <= 360
    assert len(set(main.crop((1600, 1250, 2048, 1365)).get_flattened_data())) > 1
    assert not main.getexif()


def test_pdf_watermark_preserves_content_and_rotation():
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(pdf_bytes())).pages[0]).rotate(90)
    source = io.BytesIO()
    writer.write(source)
    result = process(source.getvalue(), "application/pdf")
    output = PdfReader(io.BytesIO(result.body))
    assert len(output.pages) == 1
    assert "A CloudVault document" in output.pages[0].extract_text()
    assert "CloudVault / Processed" in output.pages[0].extract_text()
    assert output.pages[0].rotation == 0
    assert result.thumbnail is None


def test_pdf_rejects_encryption_and_page_limit():
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("private")
    data = io.BytesIO()
    writer.write(data)
    with pytest.raises(InvalidFile):
        process(data.getvalue(), "application/pdf")
    writer = PdfWriter()
    for _ in range(101):
        writer.add_blank_page(100, 100)
    data = io.BytesIO()
    writer.write(data)
    with pytest.raises(InvalidFile):
        process(data.getvalue(), "application/pdf")


def test_invalid_or_mismatched_file_types_are_rejected():
    with pytest.raises(InvalidFile):
        process(image_bytes(), "image/jpeg")
    with pytest.raises(InvalidFile):
        process(b"invalid", "application/pdf")
    with pytest.raises(InvalidFile):
        process(image_bytes(size=(5000, 4100)), "image/png")


def test_isolated_processor_runs_and_enforces_timeout(monkeypatch):
    result = process_isolated(pdf_bytes(), "application/pdf", timeout=10)
    assert result.body.startswith(b"%PDF-")
    import subprocess

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("processor", 1)

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(ProcessingTimeout):
        process_isolated(pdf_bytes(), "application/pdf", timeout=1)


def test_worker_processes_image_updates_all_references_and_accounts_for_thumbnail(cloud):
    file = queued_image(cloud)
    duplicate = cloud.client.post(
        "/api/files/check-hash",
        json={
            "sha256": file["sha256"],
            "filename": "copy.png",
        },
    )
    assert duplicate.json()["duplicate"]
    worker = Worker(cloud.app.state.services, cloud.settings)
    assert worker.run_once(0) == 1
    master = master_for(cloud, file)
    assert master["status"] == "READY"
    assert master["attempt_count"] == 1
    assert master["processed_size_bytes"] > 0
    assert master["thumbnail_size_bytes"] > 0
    assert "lease_owner" not in master
    for key in ["processed_s3_key", "thumbnail_s3_key"]:
        obj = cloud.s3.get_object(Bucket=cloud.settings.processed_bucket, Key=master[key])
        with obj["Body"] as stream:
            assert Image.open(io.BytesIO(stream.read())).format == "JPEG"
    files = cloud.client.get("/api/files").json()["files"]
    assert len(files) == 2 and all(f["status"] == "READY" for f in files)
    stats = cloud.client.get("/api/stats").json()
    assert (
        stats["processed_bytes"] == master["processed_size_bytes"] + master["thumbnail_size_bytes"]
    )
    assert stats["unique_raw_bytes"] == file["size_bytes"]
    assert not cloud.app.state.services.queue.receive(0)
    for variant in ["original", "processed", "thumbnail"]:
        assert (
            cloud.client.get(f"/api/files/{file['file_id']}/download?variant={variant}").status_code
            == 200
        )


def test_worker_processes_pdf_and_acknowledges_duplicate_ready_delivery(cloud):
    file = confirmed(cloud, pdf_bytes())
    worker = Worker(cloud.app.state.services, cloud.settings)
    message = take_message(cloud)
    assert worker.handle_message(message) == "READY"
    master = master_for(cloud, file)
    cloud.app.state.services.queue.enqueue(master)
    assert worker.handle_message(take_message(cloud)) == "READY"
    assert master_for(cloud, file)["attempt_count"] == 1


def test_only_one_live_lease_and_stale_completion_cannot_win(cloud):
    file = queued_image(cloud)
    state = WorkerState(cloud.app.state.services.database)
    master = master_for(cloud, file)
    first = state.claim(master, "one")
    assert first
    assert state.claim(master, "two") is None
    cloud.db.update_item(
        TableName=cloud.settings.files_table,
        Key=encode({"file_hash": master["file_hash"]}),
        UpdateExpression="SET lease_until=:past",
        ExpressionAttributeValues=encode({":past": 0}),
    )
    second = state.claim(master, "two")
    assert second["attempt_count"] == 2
    assert state.complete(first, "one", {"processed_size_bytes": 1}) is None
    assert state.renew(second, "two")
    assert state.complete(second, "two", {"processed_size_bytes": 2})["status"] == "READY"


def test_retries_three_times_marks_failed_and_preserves_message_for_dlq(cloud):
    file = confirmed(cloud, b"not a valid PDF")
    worker = Worker(cloud.app.state.services, cloud.settings)
    for attempt in range(1, 4):
        message = take_message(cloud)
        result = worker.handle_message(message)
        assert result == ("RETRY" if attempt < 3 else "FAILED")
        master = master_for(cloud, file)
        assert master["attempt_count"] == attempt
        assert master["last_error"]
        reset_visibility(cloud, message)
        if attempt < 3:
            cloud.db.update_item(
                TableName=cloud.settings.files_table,
                Key=encode({"file_hash": master["file_hash"]}),
                UpdateExpression="SET retry_at=:past",
                ExpressionAttributeValues=encode({":past": 0}),
            )
    assert worker.handle_message(take_message(cloud)) == "FAILED"
    assert master_for(cloud, file)["attempt_count"] == 3


def test_crashed_third_attempt_becomes_failed_without_fourth_processing_attempt(cloud):
    file = queued_image(cloud)
    worker = Worker(cloud.app.state.services, cloud.settings)
    master = master_for(cloud, file)
    cloud.db.update_item(
        TableName=cloud.settings.files_table,
        Key=encode({"file_hash": master["file_hash"]}),
        UpdateExpression="SET #s=:s, attempt_count=:n, lease_until=:expired",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=encode({":s": "PROCESSING", ":n": 3, ":expired": 0}),
    )
    assert worker.handle_message(take_message(cloud)) == "FAILED"
    assert master_for(cloud, file)["status"] == "FAILED"


def test_acknowledgement_failure_does_not_regress_ready_file(cloud, monkeypatch):
    file = queued_image(cloud)
    worker = Worker(cloud.app.state.services, cloud.settings)

    def unavailable(_):
        raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "DeleteMessage")

    monkeypatch.setattr(cloud.app.state.services.queue, "acknowledge", unavailable)
    with pytest.raises(ClientError):
        worker.handle_message(take_message(cloud))
    assert master_for(cloud, file)["status"] == "READY"


def test_invalid_payload_and_wrong_tenant_key_never_process(cloud):
    file = queued_image(cloud)
    worker = Worker(cloud.app.state.services, cloud.settings)
    msg = take_message(cloud)
    payload = json.loads(msg["Body"])
    payload["user_id"] = "bob"
    msg["Body"] = json.dumps(payload)
    assert worker.handle_message(msg) == "INVALID"
    assert master_for(cloud, file)["status"] == "QUEUED"
    msg["Body"] = "invalid JSON"
    assert worker.handle_message(msg) == "INVALID"


def test_busy_lease_defers_message_without_acknowledging_or_incrementing_attempts(cloud):
    file = queued_image(cloud)
    worker = Worker(cloud.app.state.services, cloud.settings)
    assert worker.state.claim(master_for(cloud, file), "existing")
    assert worker.handle_message(take_message(cloud)) == "BUSY"
    assert master_for(cloud, file)["attempt_count"] == 1


def test_heartbeat_extends_message_and_lease(cloud):
    from worker.runner import Heartbeat

    file = queued_image(cloud)
    worker = Worker(cloud.app.state.services, cloud.settings)
    master = worker.state.claim(master_for(cloud, file), "one")
    message = take_message(cloud)
    heartbeat = Heartbeat(
        worker.state, cloud.app.state.services.queue, master, "one", message["ReceiptHandle"]
    )
    heartbeat.renew()
    assert master_for(cloud, file)["lease_until"] >= int(time.time()) + 100


def test_retryable_storage_failure_is_reported_without_leaking_sdk_details(cloud, monkeypatch):
    file = queued_image(cloud)
    worker = Worker(cloud.app.state.services, cloud.settings)

    def unavailable(*_):
        raise ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "private-resource"}}, "GetObject"
        )

    monkeypatch.setattr(cloud.app.state.services.storage, "read_verified", unavailable)
    assert worker.handle_message(take_message(cloud)) == "RETRY"
    assert "private-resource" not in master_for(cloud, file)["last_error"]
