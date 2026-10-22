import hashlib
from types import SimpleNamespace

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from backend.config import Settings
from backend.main import create_app
from backend.services.storage import checksum

ALICE_TOKEN = "alice-test-token-" + "a" * 32
BOB_TOKEN = "bob-test-token-" + "b" * 32
DATA = b"%PDF-1.7\nCloudVault test document\n"


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    with mock_aws():
        session = boto3.Session(region_name="us-east-1")
        s3 = session.client("s3")
        s3.create_bucket(Bucket="cloudvault-test-raw")
        s3.create_bucket(Bucket="cloudvault-test-processed")
        db = session.client("dynamodb")
        for table, keys in [
            ("CloudVault_Files", [("file_hash", "HASH")]),
            ("CloudVault_UserFiles", [("user_id", "HASH"), ("file_id", "RANGE")]),
        ]:
            db.create_table(
                TableName=table,
                KeySchema=[{"AttributeName": name, "KeyType": kind} for name, kind in keys],
                AttributeDefinitions=[
                    {"AttributeName": name, "AttributeType": "S"} for name, _ in keys
                ],
                ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            )
        sqs = session.client("sqs")
        queue_url = sqs.create_queue(QueueName="cloudvault-test-jobs")["QueueUrl"]
        settings = Settings(
            _env_file=None,
            aws_region="us-east-1",
            raw_bucket="cloudvault-test-raw",
            processed_bucket="cloudvault-test-processed",
            queue_url=queue_url,
            tenant_tokens={"alice": ALICE_TOKEN, "bob": BOB_TOKEN},
        )
        app = create_app(settings)
        with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
            yield SimpleNamespace(
                client=client,
                app=app,
                s3=s3,
                sqs=sqs,
                db=db,
                settings=settings,
            )


def initiate(cloud, data=DATA, **overrides):
    body = {
        "filename": "notes.pdf",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "content_type": "application/pdf",
    }
    body.update(overrides)
    return cloud.client.post("/api/files/initiate-upload", json=body)


def upload(cloud, data=DATA, tenant="alice"):
    digest = hashlib.sha256(data).hexdigest()
    cloud.s3.put_object(
        Bucket=cloud.settings.raw_bucket,
        Key=f"{tenant}/{digest}",
        Body=data,
        ContentType="application/pdf",
        ChecksumAlgorithm="SHA256",
        ChecksumSHA256=checksum(digest),
    )


def confirmed(cloud, data=DATA):
    result = initiate(cloud, data).json()
    upload(cloud, data)
    response = cloud.client.post(
        "/api/files/confirm-upload",
        json={"file_id": result["file"]["file_id"]},
    )
    assert response.status_code == 200, response.text
    return response.json()
