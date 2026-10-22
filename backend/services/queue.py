import json


class JobQueue:
    def __init__(self, client, queue_url):
        self.client = client
        self.queue_url = queue_url

    def enqueue(self, master):
        self.client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(
                {
                    "version": 1,
                    "file_hash": master["file_hash"],
                    "sha256": master["sha256"],
                    "s3_key": master["raw_key"],
                    "user_id": master["user_id"],
                }
            ),
        )

    def receive(self, wait_seconds=20):
        return self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=120,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        ).get("Messages", [])

    def acknowledge(self, receipt):
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt)

    def defer(self, receipt, seconds):
        self.client.change_message_visibility(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt,
            VisibilityTimeout=max(0, min(43200, int(seconds))),
        )
