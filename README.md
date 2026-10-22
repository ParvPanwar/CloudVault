# CloudVault

UCS531 Cloud Computing project: direct S3 file uploads, tenant-scoped SHA-256
deduplication, DynamoDB metadata, and asynchronous jobs in SQS.

**Implemented:** core FastAPI backend, authenticated tenant access, upload verification,
duplicate references, original/processed download signing, paginated file lists,
storage metrics, AWS adapter tests, and GitHub Actions checks.

The AWS foundation is deployed in Sydney: private S3 buckets, DynamoDB tables,
SQS/DLQ, a scoped runtime role/profile, and email cost alerts. It is defined in
[infra/core.yaml](infra/core.yaml) and [infra/budget.yaml](infra/budget.yaml).
Deployment and cost decisions are documented in [infra/README.md](infra/README.md).

**Next:** SQS worker (PDF/image processing), dashboard, AWS deployment, academic report.
The API queues jobs; it does not process them yet. New confirmed files stay `QUEUED`
until a worker is implemented and running.

## Run locally

Requires Python 3.12 or later. From the project root:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Edit `.env`: set bucket names, queue URL, and a separate random application token for
each tenant. Generate tokens with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.
These tokens are application credentials, not AWS credentials. Keep `.env` private.
Configure AWS credentials through your local AWS profile or an EC2 instance role;
the application uses boto3's standard credential chain.

```sh
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

- Interactive API: <http://127.0.0.1:8000/docs> — click **Authorize** and paste a tenant token.
- Liveness: <http://127.0.0.1:8000/health> — does not check AWS connectivity.
- OpenAPI: <http://127.0.0.1:8000/openapi.json>.

The server can start without AWS configuration. Authenticated file requests return
`503` until resource settings are supplied. Without tenant tokens, all file requests
return `401`. Local tests need neither an AWS account nor real credentials.

## AWS Agent Toolkit

The local toolkit is configured with the `cloudvault` AWS CLI profile and selected
Region `ap-southeast-2` (Sydney), using the new AWS experience. The toolkit's MCP
endpoint uses `us-east-1`; project resources must stay in `ap-southeast-2`.
Confirm the selected Region in AWS Settings → View all projects → Overview →
Additional Info → Region before provisioning resources.

Renew browser-based sign-in when needed:

```sh
aws login --profile cloudvault --region ap-southeast-2
```

Toolkit skills and MCP settings are installed locally, outside this repository.
CloudVault-specific development instructions live in `AGENTS.md`.
Infrastructure templates and deployment notes live in [infra/](infra/README.md).

## API and upload flow

All `/api/*` routes require `Authorization: Bearer <tenant-token>`. The server derives
the tenant from that credential; clients cannot choose a `user_id` in the request.

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/files/check-hash` | Link a confirmed same-tenant file, or report no duplicate |
| POST | `/api/files/initiate-upload` | Reserve metadata and return a 300-second signed PUT |
| POST | `/api/files/confirm-upload` | Verify the S3 object, enqueue it, and return its status |
| GET | `/api/files?limit=50&cursor=...` | List tenant references; follow `next_cursor` |
| GET | `/api/files/{file_id}/download?variant=original` | Return a 60-second signed GET |
| GET | `/api/files/{file_id}/download?variant=processed` | Download processed content when `READY` |
| GET | `/api/stats` | Tenant-local logical, physical, and deduplication metrics |

1. Hash the file locally as lowercase hex SHA-256.
2. Call `check-hash` with `sha256` and `filename`. A successful duplicate check **creates
   a new logical file reference**; it is not a read-only lookup. On `duplicate: true`, stop.
3. Otherwise call `initiate-upload` with those fields plus `size_bytes` and
   `content_type` (`application/pdf`, `image/png`, or `image/jpeg`; default limit 50 MiB).
   It also handles duplicates, so callers may skip the separate hash check.
4. On `duplicate: false`, PUT the raw file bytes to `upload_url`, supplying **every**
   header in `upload_headers`. Do not send the application bearer token to S3 or use
   multipart/form-data. Content-Length is signed; HTTP clients must send the actual
   file length. Browsers set that header automatically when the body is a `File`/`Blob`.
5. Call `confirm-upload` with `{"file_id":"<returned file_id>"}`.
6. Poll the file list for `QUEUED → PROCESSING → READY`. Original downloads work after
   confirmation; processed downloads require `READY` and a processed object key.

The signed PUT binds content length, type, checksum, and `If-None-Match: *` to prevent
replacement. S3 validates the checksum; confirmation checks the stored checksum,
length, and type before sending a job. See AWS's
[upload integrity documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html)
and [conditional PUT behavior](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html).
Declared MIME type is metadata, not a file-content parser; the worker must validate
the actual format before processing it.

If a PUT gets `412`, an object already exists at that key; try confirmation. If a
request fails with `503`, retry confirmation on the same reference. An expired PUT
can be refreshed by calling initiate again with the same hash and metadata. New
initiate/check-hash calls create new logical references; they are not idempotency-key APIs.

### Example upload

After configuring AWS and `.env`, save this as a temporary local Python script and
run it from the project root with an actual `notes.pdf`. It uses `httpx` from the dev dependencies:

```python
import hashlib
from pathlib import Path

import httpx
from backend.config import Settings

settings = Settings()
path = Path("notes.pdf")
data = path.read_bytes()
with httpx.Client(
    base_url="http://127.0.0.1:8000",
    headers={"Authorization": f"Bearer {settings.tenant_tokens['alice']}"},
) as api:
    response = api.post("/api/files/initiate-upload", json={
        "filename": path.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "content_type": "application/pdf",
    })
    response.raise_for_status()
    upload = response.json()
    if not upload["duplicate"]:
        # Separate client: never forward the application bearer token to S3.
        result = httpx.put(
            upload["upload_url"], content=data,
            headers=upload["upload_headers"], timeout=60,
        )
        if result.status_code != 412:
            result.raise_for_status()
        response = api.post("/api/files/confirm-upload", json={
            "file_id": upload["file"]["file_id"],
        })
        response.raise_for_status()
        print(response.json())
    else:
        print(upload["file"])
```

## Data and security decisions

- `CloudVault_Files`: string partition key `file_hash = <tenant>:<sha256>`.
  Stores checksum, raw key, size, content type, and processing status.
- `CloudVault_UserFiles`: string partition key `user_id`, string sort key `file_id`.
  Stores each logical reference's filename and creation time. Names never become S3 paths.
- First metadata reservation and reference creation are transactional. Concurrent
  attempts converge on one master object; conditional PUTs prevent overwrites.
- **Deduplication is currently within a tenant.** The original plans describe global
  deduplication. Knowing a file hash is insufficient proof of ownership, so the API
  does not disclose or link another tenant's content. Cross-tenant deduplication needs
  a proof-of-ownership design before it can be enabled safely.
- Tenant tokens provide a small demo authentication mechanism. A dashboard must not
  bundle all users' tokens. Use individual sessions/OIDC for an untrusted public service,
  and HTTPS whenever credentials leave localhost.
- Downloads are temporary bearer links: anyone holding the URL can use it until expiry.
  API responses use `Cache-Control: no-store`; avoid logging signed URLs and tokens.

## Metrics and retry semantics

Metrics count confirmed logical references, unique raw bytes, and worker-reported
`processed_size_bytes`. Deduplication savings = logical bytes − unique raw bytes;
physical bytes = unique raw bytes + processed bytes. Pending references are counted
separately and contribute no byte savings. These are metadata-derived values, not
an S3 inventory or a billing estimate. Abandoned uploads and externally modified
objects are not reconciled yet. No dollar-saving estimate is fabricated.

S3, DynamoDB, and SQS do not share a transaction. Confirmation sends the job before
marking the metadata `QUEUED`, leaving failed sends retryable. A crash after sending
can cause another message on retry; SQS standard queues can also redeliver. The
[worker contract](worker/README.md) requires idempotent processing. The API never
regresses a fast worker's status back to `QUEUED`.

## Verify

```sh
pytest -q
ruff check backend tests
ruff format --check backend tests
```

The tests use Moto's in-process S3, DynamoDB, and SQS implementations with dummy
credentials. They cover authorization, isolation, signed upload constraints, object
verification, transactional reservations, duplicate linking, pagination, metrics,
download signing, queue failures, and worker/API state races. Signature enforcement,
browser CORS and end-to-end file transfers still need a browser integration check.
The local backend has also passed read-only checks against real DynamoDB tables
using the browser-login profile; those checks write no sample files or jobs.

For the exact dependency set tested at this milestone, install
`pip install -r requirements-dev.lock` followed by `pip install -e . --no-deps`.

## Layout

```text
backend/          FastAPI app, configuration, routes, AWS service adapters
tests/            Offline API and AWS adapter tests
infra/            CloudFormation templates and deployment notes
worker/           Queue contract for the next implementation phase
.github/workflows/ Continuous integration
```

The original [project context](project_context.md) and [implementation plan](implementation_plan.md)
remain the design reference; this README describes the implemented behavior.
The project's $0 target depends on account eligibility, region, usage, and current AWS
terms. The historical free-tier guarantees in those plans have not been verified.
No AWS resources are provisioned by installing, testing, or starting this backend.
