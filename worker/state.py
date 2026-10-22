import time

from backend.services.database import decode, encode

LEASE_SECONDS = 120
MAX_ATTEMPTS = 3


class WorkerState:
    def __init__(self, database):
        self.database = database
        self.client = database.client

    def update(self, file_hash, expression, condition, values):
        try:
            result = self.client.update_item(
                TableName=self.database.files_table,
                Key=encode({"file_hash": file_hash}),
                UpdateExpression=expression,
                ConditionExpression=condition,
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues=encode(values),
                ReturnValues="ALL_NEW",
            )
        except self.client.exceptions.ConditionalCheckFailedException:
            return None
        return decode(result["Attributes"])

    def claim(self, master, owner):
        now = int(time.time())
        return self.update(
            master["file_hash"],
            "SET #s=:processing, lease_owner=:owner, lease_until=:until, "
            "attempt_count=if_not_exists(attempt_count,:zero)+:one, updated_at=:now "
            "REMOVE last_error, retry_at",
            "attribute_exists(file_hash) AND "
            "(attribute_not_exists(attempt_count) OR attempt_count < :max) AND "
            "(#s IN (:pending,:queued) OR (#s=:processing AND lease_until <= :now))",
            {
                ":processing": "PROCESSING",
                ":pending": "PENDING_UPLOAD",
                ":queued": "QUEUED",
                ":owner": owner,
                ":until": now + LEASE_SECONDS,
                ":now": now,
                ":zero": 0,
                ":one": 1,
                ":max": MAX_ATTEMPTS,
            },
        )

    def renew(self, master, owner):
        now = int(time.time())
        return self.update(
            master["file_hash"],
            "SET lease_until=:until",
            "#s=:processing AND lease_owner=:owner",
            {":until": now + LEASE_SECONDS, ":processing": "PROCESSING", ":owner": owner},
        )

    def complete(self, master, owner, artifacts):
        now = int(time.time())
        assignments = ", ".join(f"{field}=:{field}" for field in artifacts)
        return self.update(
            master["file_hash"],
            f"SET #s=:ready, updated_at=:now, {assignments} "
            "REMOVE lease_owner, lease_until, retry_at, last_error",
            "#s=:processing AND lease_owner=:owner AND lease_until > :now",
            {
                ":ready": "READY",
                ":processing": "PROCESSING",
                ":owner": owner,
                ":now": now,
                **{f":{field}": value for field, value in artifacts.items()},
            },
        )

    def failed_attempt(self, master, owner, error):
        now = int(time.time())
        terminal = master["attempt_count"] >= MAX_ATTEMPTS
        delay = 5 * 2 ** (int(master["attempt_count"]) - 1)
        return self.update(
            master["file_hash"],
            "SET #s=:next, last_error=:error, retry_at=:retry, updated_at=:now "
            "REMOVE lease_owner, lease_until",
            "#s=:processing AND lease_owner=:owner AND lease_until > :now",
            {
                ":next": "FAILED" if terminal else "QUEUED",
                ":processing": "PROCESSING",
                ":owner": owner,
                ":now": now,
                ":error": error,
                ":retry": now + delay,
            },
        )

    def exhaust(self, master):
        now = int(time.time())
        return self.update(
            master["file_hash"],
            "SET #s=:failed, last_error=:error, updated_at=:now "
            "REMOVE lease_owner, lease_until, retry_at",
            "attempt_count >= :max AND (#s=:queued OR (#s=:processing AND lease_until <= :now))",
            {
                ":failed": "FAILED",
                ":queued": "QUEUED",
                ":processing": "PROCESSING",
                ":now": now,
                ":max": MAX_ATTEMPTS,
                ":error": "Processing stopped after three unsuccessful attempts.",
            },
        )
