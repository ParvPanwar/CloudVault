# Worker contract — next phase

No worker is implemented yet. The backend sends this versioned JSON body to the
standard SQS queue after verifying the uploaded S3 object's checksum, size, and type:

```json
{
  "version": 1,
  "file_hash": "alice:<64-character lowercase SHA-256>",
  "sha256": "<64-character lowercase SHA-256>",
  "s3_key": "alice/<sha256>",
  "user_id": "alice"
}
```

`file_hash` identifies the master record in `CloudVault_Files`, not an individual
logical reference. Filenames live on user references and are not processing inputs.
Workers must re-read the master and use its trusted raw key, size, checksum, and
content type. Reject inconsistent or unsupported message versions/payloads.

Processing requirements:

1. Long poll with `WaitTimeSeconds=20`. SQS delivery is at least once.
2. Claim the master with a conditional write from `PENDING_UPLOAD` **or** `QUEUED`
   to `PROCESSING`: a message can arrive before confirmation marks it `QUEUED`.
   Use an expiring lease to recover from worker crashes and exclude concurrent workers.
3. A `READY` record needs no further work; safely acknowledge duplicate delivery.
4. Validate actual file contents and resource limits before processing with Pillow/pypdf.
   Do not trust the declared MIME type or assume PDF compression reduces every file.
5. Write output under a deterministic tenant-scoped processed key. Record
   `processed_s3_key` and integer `processed_size_bytes`, then conditionally mark
   the master `READY` using the active lease. Never overwrite another tenant's output.
6. Delete the SQS message only after a committed successful result. Extend visibility
   during long tasks and recover expired leases on retry. Coordinate permanent
   `FAILED` records with the DLQ policy rather than silently dropping failed messages.

All logical references read their processing state from this master record. A worker
must not create per-reference processed copies or charge their sizes multiple times.
