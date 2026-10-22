from datetime import UTC, datetime
from uuid import uuid4

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def encode(item):
    return {key: _serializer.serialize(value) for key, value in item.items()}


def decode(item):
    return {key: _deserializer.deserialize(value) for key, value in item.items()}


class Database:
    def __init__(self, client, files_table, user_files_table):
        self.client = client
        self.files_table = files_table
        self.user_files_table = user_files_table

    def get_hash(self, user_id, sha256):
        result = self.client.get_item(
            TableName=self.files_table,
            Key=encode({"file_hash": f"{user_id}:{sha256}"}),
            ConsistentRead=True,
        )
        return decode(result["Item"]) if "Item" in result else None

    def get_reference(self, user_id, file_id):
        result = self.client.get_item(
            TableName=self.user_files_table,
            Key=encode({"user_id": user_id, "file_id": file_id}),
            ConsistentRead=True,
        )
        return decode(result["Item"]) if "Item" in result else None

    def reference(self, user_id, filename, file_hash):
        return {
            "user_id": user_id,
            "file_id": uuid4().hex,
            "file_hash": file_hash,
            "filename": filename,
            "created_at": datetime.now(UTC).isoformat(),
        }

    def link(self, user_id, filename, master):
        ref = self.reference(user_id, filename, master["file_hash"])
        self.client.put_item(TableName=self.user_files_table, Item=encode(ref))
        return ref

    def reserve(self, user_id, upload):
        master = {
            "file_hash": f"{user_id}:{upload.sha256}",
            "sha256": upload.sha256,
            "user_id": user_id,
            "size_bytes": upload.size_bytes,
            "content_type": upload.content_type,
            "raw_key": f"{user_id}/{upload.sha256}",
            "status": "PENDING_UPLOAD",
            "created_at": datetime.now(UTC).isoformat(),
        }
        ref = self.reference(user_id, upload.filename, master["file_hash"])
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.files_table,
                            "Item": encode(master),
                            "ConditionExpression": "attribute_not_exists(file_hash)",
                        }
                    },
                    {"Put": {"TableName": self.user_files_table, "Item": encode(ref)}},
                ]
            )
        except ClientError as exc:
            reasons = exc.response.get("CancellationReasons", [])
            if not reasons or reasons[0].get("Code") != "ConditionalCheckFailed":
                raise
            master = self.get_hash(user_id, upload.sha256)
            if master is None:
                raise
            if master["size_bytes"] != upload.size_bytes:
                raise ValueError("Hash already registered with a different size") from exc
            ref = self.link(user_id, upload.filename, master)
        return ref, master

    def mark_queued(self, master):
        try:
            self.client.update_item(
                TableName=self.files_table,
                Key=encode({"file_hash": master["file_hash"]}),
                UpdateExpression="SET #status = :queued",
                ConditionExpression="#status = :pending",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=encode(
                    {
                        ":queued": "QUEUED",
                        ":pending": "PENDING_UPLOAD",
                    }
                ),
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def list_references(self, user_id, limit=100, cursor=None):
        params = {
            "TableName": self.user_files_table,
            "KeyConditionExpression": "user_id = :user",
            "ExpressionAttributeValues": encode({":user": user_id}),
            "ConsistentRead": True,
            "Limit": limit,
        }
        if cursor:
            params["ExclusiveStartKey"] = encode({"user_id": user_id, "file_id": cursor})
        result = self.client.query(**params)
        last_key = decode(result["LastEvaluatedKey"]) if result.get("LastEvaluatedKey") else {}
        return [decode(item) for item in result["Items"]], last_key.get("file_id")
