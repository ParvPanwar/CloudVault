import base64

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

    def presign_download(self, master, variant):
        processed = variant == "processed"
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.processed_bucket if processed else self.raw_bucket,
                "Key": master["processed_s3_key"] if processed else master["raw_key"],
                "ResponseContentDisposition": "attachment",
                "ResponseContentType": "application/octet-stream",
            },
            ExpiresIn=60,
            HttpMethod="GET",
        )
