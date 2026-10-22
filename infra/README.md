# AWS prerequisites for the core backend

No provisioning runs automatically. Create the following resources in one AWS
account and region, and put their names/URL in `.env`. Verify your account's current
free-tier eligibility and set billing alerts before provisioning. Alerts do not cap spending.

1. Two private general-purpose S3 buckets: raw uploads and processed output. Keep
   Block Public Access enabled and use S3-managed encryption (SSE-S3) for this milestone.
   The raw bucket needs a CORS policy matching your dashboard origin; see
   [s3-cors.example.json](s3-cors.example.json). Replace the localhost origin when deploying.
   Do not put an automatic expiration policy on all raw objects: confirmed originals
   are the source for downloads. Abandoned-upload cleanup needs metadata awareness.
2. DynamoDB table `CloudVault_Files`: partition key `file_hash` (String), no sort key.
3. DynamoDB table `CloudVault_UserFiles`: partition key `user_id` (String), sort key
   `file_id` (String). No secondary indexes are required. For the small demo, start
   with provisioned 5 RCU/5 WCU per table, autoscaling disabled, subject to your
   account's limits and eligibility. Transactions consume extra capacity.
4. Standard SQS queue `cloudvault-job-queue`, plus `cloudvault-dlq`; configure redrive
   after 3 receives. Set an initial visibility timeout of 120 seconds; the future
   worker must extend it for longer jobs. Use SQS-managed encryption for this milestone.
5. A local AWS profile for development, or an EC2 instance role for deployment, with
   the permissions below. The API does not create or administer resources.

## API runtime permissions

Scope every resource ARN to the actual account, region, table, queue, and bucket.

| Actions | Resource |
| --- | --- |
| `s3:PutObject`, `s3:GetObject` | `arn:aws:s3:::RAW_BUCKET/*` |
| `s3:GetObject` | `arn:aws:s3:::PROCESSED_BUCKET/*` |
| `s3:ListBucket` | `arn:aws:s3:::RAW_BUCKET` (allows HEAD on absent keys to report 404) |
| `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:Query` | Both table ARNs |
| `sqs:SendMessage` | Job queue ARN |

DynamoDB transactions are authorized using the underlying `PutItem`/`UpdateItem`
permissions; see [AWS transaction IAM guidance](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html).
The worker will additionally need raw-object reads, processed-object writes, metadata
updates, and SQS receive/delete/change-visibility permissions. Add those with the worker,
not to the API-only role. KMS encryption requires additional key policies and permissions
and is outside this setup.

The API never runs a DynamoDB `Scan` and never accepts an S3 key or tenant ID from the
client. Authentication and tenant scoping happen in the API; the service role can access
the configured project resources, so protect its credentials and the host.

## Live integration checklist

- Start the API using the intended profile/role, initiate an upload, and PUT using
  all returned headers. Confirm that an incorrect checksum or a replacement PUT fails.
- Verify the browser preflight succeeds for the dashboard origin. Use HTTPS outside
  localhost; browser SHA-256 through Web Crypto needs a secure context.
- Confirm a real object, inspect its DynamoDB state and the SQS message. Retry
  confirmation and ensure it returns the current state.
- Upload again as the same tenant: no second S3 object. Another tenant must see no
  duplicate and must receive 404 for the first tenant's file ID.
- Test original download expiry. Processed downloads will remain unavailable until
  the worker writes `READY`, `processed_s3_key`, and `processed_size_bytes`.
- Review actual AWS usage and remove demo resources when finished. Nothing here
  guarantees a zero bill or acts as a spending limit.
