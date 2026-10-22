# Dashboard — next phase

The dashboard is not implemented yet. It will use the FastAPI routes documented in
the root README, calculate SHA-256 locally, PUT directly to S3 with the exact returned
headers, and poll for worker status. The API accepts PNG, JPEG, and PDF files.

Supply each user only their own application token. A demo tenant switcher must ask
for the selected user's token; do not ship every tenant's credential in frontend code.
Configure the same explicit origin in the API and raw S3 bucket CORS policies.

Deduplication is currently tenant-scoped: demonstrate it by selecting the same file
twice as Alice, then demonstrate isolation by switching to Bob. Cross-tenant duplicate
linking remains deferred until file ownership can be proved securely.
