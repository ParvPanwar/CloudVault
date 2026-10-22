# CloudVault worker

The worker consumes the versioned SQS job emitted after a verified upload. Run it
alongside the API with the same `.env` and AWS credentials:

```sh
python -m worker
```

`python -m worker --once` runs a single long-poll cycle.

For every message, the worker re-reads trusted master metadata, conditionally claims
a 120-second DynamoDB lease, verifies the raw S3 object's checksum and size, then
processes it in a bounded subprocess. Images are resized, stripped of metadata,
watermarked, and thumbnailed. PDFs are watermarked and have active annotations
removed. Processed assets use deterministic content-addressed S3 keys.

The lease and conditional completion update make duplicate SQS delivery safe. Failed
attempts return to `QUEUED` with exponential backoff; after three attempts the master
is `FAILED` and the message remains available for the configured SQS DLQ policy.
The worker enforces a 50 MiB input limit, 25 MiB output limit, 20 MP image limit,
100-page PDF limit, and the configured 1–90 second processing timeout. It does not
perform malware scanning.
