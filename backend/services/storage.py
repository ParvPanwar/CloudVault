import base64
import hashlib
from urllib.parse import quote

from botocore.exceptions import ClientError


def checksum(sha256):
    return base64.b64encode(bytes.fromhex(sha256)).decode("ascii")


class Storage:
    def __init__(self, client, raw_bucket, processed_bucket):
        self.client = client
        self.raw_bucket = raw_bucket
        self.processed_bucket = processed_bucket

    def presign_upload(self, master):
        digest = checksum(master["sha256"])
        url = self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.raw_bucket,
                "Key": master["raw_key"],
                "ContentType": master["content_type"],
                "ContentLength": int(master["size_bytes"]),
                "ChecksumAlgorithm": "SHA256",
                "ChecksumSHA256": digest,
                "IfNoneMatch": "*",
            },
            ExpiresIn=300,
            HttpMethod="PUT",
        )
        return url, {
            "Content-Type": master["content_type"],
            "x-amz-checksum-sha256": digest,
            "x-amz-sdk-checksum-algorithm": "SHA256",
            "If-None-Match": "*",
        }

    def verify_upload(self, master):
        try:
            obj = self.client.head_object(
                Bucket=self.raw_bucket,
                Key=master["raw_key"],
                ChecksumMode="ENABLED",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return (
            obj["ContentLength"] == master["size_bytes"]
            and obj.get("ChecksumSHA256") == checksum(master["sha256"])
            and obj.get("ContentType") == master["content_type"]
        )

    def presign_download(self, master, variant, filename=None):
        processed = variant != "original"
        key = {
            "original": "raw_key",
            "processed": "processed_s3_key",
            "thumbnail": "thumbnail_s3_key",
        }[variant]
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.processed_bucket if processed else self.raw_bucket,
                "Key": master[key],
                "ResponseContentDisposition": (
                    "attachment; filename*=UTF-8''" + quote(filename, safe="")
                    if filename
                    else "attachment"
                ),
                "ResponseContentType": "application/octet-stream",
            },
            ExpiresIn=60,
            HttpMethod="GET",
        )

    def read_verified(self, master, max_bytes):
        obj = self.client.get_object(Bucket=self.raw_bucket, Key=master["raw_key"])
        with obj["Body"] as stream:
            if obj["ContentLength"] > max_bytes:
                raise ValueError("File exceeds processing limit")
            body = stream.read(max_bytes + 1)
        if (
            len(body) != master["size_bytes"]
            or len(body) > max_bytes
            or hashlib.sha256(body).hexdigest() != master["sha256"]
        ):
            raise ValueError("Stored file failed integrity checks")
        return body

    def put_artifact(self, master, body, extension, content_type, kind):
        digest = hashlib.sha256(body).hexdigest()
        # Content-addressed outputs prevent a stale lease from replacing a winner's bytes.
        key = f"{master['user_id']}/{master['sha256']}/{kind}-v1-{digest}.{extension}"
        try:
            self.client.put_object(
                Bucket=self.processed_bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                ChecksumAlgorithm="SHA256",
                ChecksumSHA256=checksum(digest),
                IfNoneMatch="*",
            )
        except self.client.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] != "PreconditionFailed":
                raise
        return key
