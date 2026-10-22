import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import ClientError

from backend.services.storage import checksum
from tests.conftest import BOB_TOKEN, DATA, confirmed, initiate, upload


def test_health_and_authentication(cloud):
    assert cloud.client.get("/health").json() == {"status": "ok"}
    for headers in [{"Authorization": ""}, {"Authorization": "Bearer invalid"}]:
        result = cloud.client.get("/api/files", headers=headers)
        assert result.status_code == 401
        assert result.headers["WWW-Authenticate"] == "Bearer"
    assert cloud.client.get("/api/files").json() == {"files": [], "next_cursor": None}


def test_initiate_upload_signs_checksum_size_type_and_overwrite_guard(cloud):
    result = initiate(cloud)
    assert result.status_code == 200
    body = result.json()
    assert body["file"]["status"] == "PENDING_UPLOAD"
    query = parse_qs(urlparse(body["upload_url"]).query)
    assert query["X-Amz-Expires"] == ["300"]
    signed = query["X-Amz-SignedHeaders"][0].split(";")
    assert {
        "content-length",
        "content-type",
        "if-none-match",
        "x-amz-checksum-sha256",
        "x-amz-sdk-checksum-algorithm",
    }.issubset(signed)
    assert body["upload_headers"]["If-None-Match"] == "*"
    assert body["upload_headers"]["x-amz-checksum-sha256"]
    assert cloud.client.get("/api/stats").json()["logical_bytes"] == 0


def test_confirm_verifies_object_and_enqueues_once_on_retry(cloud):
    result = initiate(cloud).json()
    file_id = result["file"]["file_id"]
    assert (
        cloud.client.post("/api/files/confirm-upload", json={"file_id": file_id}).status_code == 409
    )
    upload(cloud)
    for _ in range(2):
        response = cloud.client.post("/api/files/confirm-upload", json={"file_id": file_id})
        assert response.status_code == 200
        assert response.json()["status"] == "QUEUED"
    messages = cloud.sqs.receive_message(QueueUrl=cloud.settings.queue_url, MaxNumberOfMessages=10)
    assert len(messages["Messages"]) == 1
    job = json.loads(messages["Messages"][0]["Body"])
    assert job["version"] == 1
    assert job["file_hash"] == f"alice:{hashlib.sha256(DATA).hexdigest()}"
    assert job["user_id"] == "alice"


@pytest.mark.parametrize("mismatch", ["size", "checksum", "type"])
def test_confirm_rejects_mismatched_object(cloud, mismatch):
    result = initiate(cloud).json()
    params = {
        "Bucket": cloud.settings.raw_bucket,
        "Key": f"alice/{hashlib.sha256(DATA).hexdigest()}",
        "Body": DATA,
        "ContentType": "application/pdf",
        "ChecksumAlgorithm": "SHA256",
    }
    if mismatch == "size":
        params["Body"] = DATA + b"extra"
    elif mismatch == "checksum":
        params["Body"] = b"x" * len(DATA)
    else:
        params["ContentType"] = "image/png"
    params["ChecksumSHA256"] = checksum(hashlib.sha256(params["Body"]).hexdigest())
    cloud.s3.put_object(**params)
    response = cloud.client.post(
        "/api/files/confirm-upload",
        json={"file_id": result["file"]["file_id"]},
    )
    assert response.status_code == 409
    assert "Messages" not in cloud.sqs.receive_message(QueueUrl=cloud.settings.queue_url)


def test_dedup_links_only_confirmed_tenant_content_and_counts_savings(cloud):
    digest = hashlib.sha256(DATA).hexdigest()
    body = {"sha256": digest, "filename": "another-copy.pdf"}
    assert cloud.client.post("/api/files/check-hash", json=body).json()["duplicate"] is False
    pending = initiate(cloud).json()
    assert cloud.client.post("/api/files/check-hash", json=body).json()["duplicate"] is False
    upload(cloud)
    cloud.client.post("/api/files/confirm-upload", json={"file_id": pending["file"]["file_id"]})
    duplicate = cloud.client.post("/api/files/check-hash", json=body).json()
    assert duplicate["duplicate"] is True
    assert duplicate["savings_bytes"] == len(DATA)
    stats = cloud.client.get("/api/stats").json()
    assert stats["total_logical_files"] == 2
    assert stats["logical_bytes"] == len(DATA) * 2
    assert stats["physical_bytes"] == len(DATA)
    assert stats["deduplication_saved_bytes"] == len(DATA)
    assert stats["bandwidth_saved_percent"] == 50.0


def test_tenant_cannot_discover_link_confirm_or_download_another_tenants_file(cloud):
    file = confirmed(cloud)
    cloud.client.headers["Authorization"] = f"Bearer {BOB_TOKEN}"
    assert cloud.client.get("/api/files").json()["files"] == []
    assert cloud.client.get("/api/stats").json()["total_logical_files"] == 0
    check = cloud.client.post(
        "/api/files/check-hash",
        json={
            "sha256": file["sha256"],
            "filename": "stolen.pdf",
        },
    )
    assert check.json()["duplicate"] is False
    assert (
        cloud.client.post(
            "/api/files/confirm-upload",
            json={
                "file_id": file["file_id"],
            },
        ).status_code
        == 404
    )
    assert cloud.client.get(f"/api/files/{file['file_id']}/download").status_code == 404
    result = initiate(cloud).json()
    assert result["duplicate"] is False
    assert "/bob/" in result["upload_url"]


def test_repeated_initiation_uses_one_master_and_no_overwrite(cloud):
    first = initiate(cloud).json()
    second = initiate(cloud).json()
    assert first["file"]["file_id"] != second["file"]["file_id"]
    assert cloud.db.scan(TableName=cloud.settings.files_table)["Count"] == 1
    assert cloud.db.scan(TableName=cloud.settings.user_files_table)["Count"] == 2
    upload(cloud)
    with pytest.raises(ClientError) as exc:
        cloud.s3.put_object(
            Bucket=cloud.settings.raw_bucket,
            Key=f"alice/{first['file']['sha256']}",
            Body=DATA,
            IfNoneMatch="*",
        )
    assert exc.value.response["Error"]["Code"] == "PreconditionFailed"


def test_same_hash_with_conflicting_size_does_not_create_reference(cloud):
    initiate(cloud)
    assert initiate(cloud, size_bytes=10).status_code == 409
    assert cloud.db.scan(TableName=cloud.settings.user_files_table)["Count"] == 1


def test_initiate_verified_duplicate_does_not_issue_upload_url(cloud):
    confirmed(cloud)
    result = initiate(cloud).json()
    assert result["duplicate"] is True
    assert result["upload_url"] is None


def test_queue_failure_preserves_retryable_upload(cloud, monkeypatch):
    result = initiate(cloud).json()
    upload(cloud)
    queue = cloud.app.state.services.queue
    original_enqueue = queue.enqueue

    def unavailable(master):
        raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "SendMessage")

    monkeypatch.setattr(queue, "enqueue", unavailable)
    response = cloud.client.post(
        "/api/files/confirm-upload",
        json={"file_id": result["file"]["file_id"]},
    )
    assert response.status_code == 503
    assert cloud.client.get("/api/files").json()["files"][0]["status"] == "PENDING_UPLOAD"
    monkeypatch.setattr(queue, "enqueue", original_enqueue)
    retry = cloud.client.post(
        "/api/files/confirm-upload",
        json={"file_id": result["file"]["file_id"]},
    )
    assert retry.json()["status"] == "QUEUED"


def test_queue_update_does_not_regress_a_fast_worker(cloud, monkeypatch):
    result = initiate(cloud).json()
    upload(cloud)
    queue = cloud.app.state.services.queue
    original_enqueue = queue.enqueue

    def fast_worker(master):
        original_enqueue(master)
        cloud.db.update_item(
            TableName=cloud.settings.files_table,
            Key={"file_hash": {"S": master["file_hash"]}},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": {"S": "PROCESSING"}},
        )

    monkeypatch.setattr(queue, "enqueue", fast_worker)
    response = cloud.client.post(
        "/api/files/confirm-upload",
        json={"file_id": result["file"]["file_id"]},
    )
    assert response.json()["status"] == "PROCESSING"


def test_pagination_and_stats_across_pages(cloud):
    file = confirmed(cloud)
    body = {"sha256": file["sha256"], "filename": "copy.pdf"}
    for _ in range(101):
        assert cloud.client.post("/api/files/check-hash", json=body).status_code == 200
    first = cloud.client.get("/api/files", params={"limit": 2}).json()
    second = cloud.client.get(
        "/api/files", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert len(first["files"]) == len(second["files"]) == 2
    assert {f["file_id"] for f in first["files"]}.isdisjoint(f["file_id"] for f in second["files"])
    assert cloud.client.get("/api/stats").json()["total_logical_files"] == 102


def test_downloads_expire_and_processed_requires_ready(cloud):
    result = initiate(cloud).json()
    path = f"/api/files/{result['file']['file_id']}/download"
    assert cloud.client.get(path).status_code == 409
    upload(cloud)
    cloud.client.post("/api/files/confirm-upload", json={"file_id": result["file"]["file_id"]})
    download = cloud.client.get(path)
    assert download.headers["Cache-Control"] == "no-store"
    assert parse_qs(urlparse(download.json()["download_url"]).query)["X-Amz-Expires"] == ["60"]
    assert cloud.client.get(path, params={"variant": "processed"}).status_code == 409
    master = cloud.app.state.services.database.get_hash("alice", result["file"]["sha256"])
    cloud.db.update_item(
        TableName=cloud.settings.files_table,
        Key={"file_hash": {"S": master["file_hash"]}},
        UpdateExpression="SET #s = :s, processed_s3_key = :key, processed_size_bytes = :size",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": {"S": "READY"},
            ":key": {"S": "alice/output.pdf"},
            ":size": {"N": "12"},
        },
    )
    processed = cloud.client.get(path, params={"variant": "processed"}).json()
    assert "cloudvault-test-processed" in processed["download_url"]
    assert cloud.client.get("/api/stats").json()["physical_bytes"] == len(DATA) + 12


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": "not-a-hash"},
        {"filename": "../secret.pdf"},
        {"filename": "a\nb.pdf"},
        {"filename": "   "},
        {"size_bytes": 0},
        {"size_bytes": True},
        {"content_type": "text/html"},
    ],
)
def test_invalid_upload_metadata(cloud, overrides):
    assert initiate(cloud, **overrides).status_code == 422


def test_upload_size_limit(cloud):
    assert initiate(cloud, size_bytes=cloud.settings.max_upload_bytes + 1).status_code == 413


def test_browser_cors_is_explicit(cloud):
    response = cloud.client.options(
        "/api/files",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = cloud.client.options(
        "/api/files",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert denied.status_code == 400
