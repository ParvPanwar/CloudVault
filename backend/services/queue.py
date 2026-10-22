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
