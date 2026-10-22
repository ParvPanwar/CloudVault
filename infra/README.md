# CloudVault AWS foundation

All project stacks deploy in **ap-southeast-2 (Sydney)**. The AWS CLI profile is
`cloudvault`. Templates are the source of truth; local outputs, email parameters,
credentials and application tokens must never be committed.

## Stacks

- `core.yaml` (`cloudvault-foundation`): two private S3 buckets, two DynamoDB tables,
  a standard SQS processing queue and DLQ, HTTPS-only bucket/queue policies, and
  an EC2 runtime IAM role and instance profile. No EC2 instance is launched.
  The stable physical-resource prefix is `cloudvault-core`; preserve it on updates.
- `budget.yaml` (`cloudvault-cost-alerts`): a monitoring-only global billing budget,
  managed by a Sydney CloudFormation stack. The budget API itself uses the global
  billing endpoint. The recipient is a `NoEcho` deployment parameter with no default.

## Cost decisions

The live eligibility check on 22 October 2026 reported an ACTIVE FREE plan, $100
in remaining promotional credits, expiration 22 April 2027, and no reported Free
Tier usage. This is a point-in-time observation, not a guarantee of future coverage.
A missing service in the usage response does not establish a free allowance.

Each DynamoDB table uses provisioned 5 RCU/5 WCU (no indexes or autoscaling), keeping
this project's total below the currently documented recurring provisioned allowance.
Tables use default AWS-owned encryption; S3 uses SSE-S3; SQS uses SSE-SQS. No
customer-managed KMS keys, paid backups, PITR, access-log buckets, or compute are
created. S3 activity can consume promotional credits; requests, storage, and transfer
must remain small. Credits are not the same as a service being permanently free.

S3 versioning is enabled. Noncurrent versions expire after one day; incomplete
multipart uploads are aborted after one day. **Current originals and processed
files never expire automatically.** Abandoned current uploads need an application-aware
cleanup job later. Do not add a blanket current-object expiry rule.

The $1 monthly budget tracks project-wide usage costs before credits/refunds. It
emails when actual cost exceeds $0.01, actual cost exceeds $1, or forecast exceeds
$1. Forecasting may require billing history. Alerts can be delayed and do not cap
spending, stop resources, or upgrade the AWS plan. No paid budget actions or reports
are configured. Check plan/credit status in AWS Settings > Billing.

Sources: [Free-plan supported services](https://docs.aws.amazon.com/accounts/latest/reference/supported-services-sign-up-new.html),
[DynamoDB recurring allowance](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Introduction.html),
[AWS Free Tier](https://aws.amazon.com/free/).

## Deploy or update

The templates support repeatable CloudFormation deployments. Review change sets
before an update that could replace a resource. The following equivalent CLI
commands are provided for humans; coding-agent AWS operations use the AWS MCP server.

```sh
aws cloudformation deploy --profile cloudvault --region ap-southeast-2 \
  --stack-name cloudvault-foundation --template-file infra/core.yaml \
  --parameter-overrides ResourcePrefix=cloudvault-core \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset

# Supply the recipient privately in your shell; do not save it in this repository.
aws cloudformation deploy --profile cloudvault --region ap-southeast-2 \
  --stack-name cloudvault-cost-alerts --template-file infra/budget.yaml \
  --parameter-overrides AlertEmail="$CLOUDVAULT_ALERT_EMAIL" \
  --no-fail-on-empty-changeset
```

The role trusts EC2 only. Its inline policy scopes object reads/writes, raw bucket
existence checks, metadata reads/writes, and queue processing to this stack's
resources. It includes worker permissions because the planned single EC2 host runs
both processes. It cannot administer resources, delete objects/tables, purge queues,
read unrelated buckets, or access KMS keys. Keep the instance profile unattached until
EC2 deployment. Human project permissions are managed separately by AWS.

IAM Policy Autopilot 0.3.0 was run against all backend AWS adapter files. Its candidate
included optional KMS, Object Lock, replication and transaction-delete actions, while
not resolving the dynamically selected presigned PUT operation. Those candidates are
not deployed verbatim: the runtime policy is scoped to the declared SSE-S3/SSE-SQS,
AWS-owned DynamoDB encryption and the backend/worker operations listed above.
For a repeatable static-analysis baseline:

```sh
uvx iam-policy-autopilot@0.3.0 generate-policies \
  "$PWD/backend/dependencies.py" "$PWD/backend/services/database.py" \
  "$PWD/backend/services/storage.py" "$PWD/backend/services/queue.py" \
  --region ap-southeast-2 --account "$AWS_ACCOUNT_ID" \
  --service-hints s3 dynamodb sqs --pretty
```

## Local configuration

Stack outputs provide `RawBucket`, `ProcessedBucket`, `FilesTable`, `UserFilesTable`,
`QueueUrl`, `QueueArn`, `DeadLetterQueueUrl`, `RuntimeRoleArn`, and
`RuntimeInstanceProfile`. Store output snapshots only under ignored `infra/.local/`.
Copy the corresponding values into `.env`, using the names in `.env.example`.
Set `AWS_PROFILE=cloudvault` in the local `.env`; leave that setting unset on EC2
so the SDK uses its instance role. The Python dependencies include CRT support for
refreshing browser-login credentials. Never
copy the browser sign-in cache or temporary AWS keys into application configuration.

The bucket CORS origin defaults to `http://localhost:5173`. Change `DashboardOrigin`
when the dashboard is deployed and set API `CORS_ORIGINS` to the same exact origin.
The API already enforces signed length/type/checksum and conditional non-overwriting
PUTs. No S3 event notification is configured: `/confirm-upload` enqueues verified jobs.

## Validation and lifecycle

The user explicitly chose AWS validation and skipped installing local cfn-lint and
cfn-guard. AWS `ValidateTemplate`, change-set pre-deployment validation, deployed
resource readbacks, and IAM policy checks provide the validation layers for this
milestone; they do not imply a local compliance scan passed.

No durable CloudTrail trail was present during setup. Standard event history and
CloudFormation events are available; long-term audit delivery is not configured.

Buckets, tables and queues use `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain`.
Tables also have deletion protection. Deleting the stack does **not** delete those
retained resources or end their potential costs. Cleanup requires an explicit,
data-aware decision, disabling table deletion protection and deleting the retained
resources separately. No cleanup or destructive action runs automatically.
