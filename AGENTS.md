# CloudVault project instructions

- CloudVault is the UCS531 storage project; keep this repository specific to its implementation and documentation.
- Use AWS Region `ap-southeast-2` and named profile `cloudvault`.
- This project uses the new AWS experience. Do not upgrade its plan or enable paid features without user authorization.
- Prefer the AWS MCP tools for AWS operations. The MCP endpoint uses `us-east-1`; project resources belong in Sydney.
- Keep infrastructure reproducible in `infra/`. Inspect CloudFormation changes before applying updates.
- Keep costs within verified allowances, and distinguish credit-covered usage from permanently free services.
- Never commit credentials, login caches, tenant tokens, alert recipients, local MCP settings, or `infra/.local/`.
- Preserve tenant isolation, signed upload checksums, conditional object writes, and retry-safe confirmation.
- Run relevant tests and lint checks before pushing changes to GitHub.
- Guidance preference: high. Explain meaningful steps and trade-offs without unnecessary confirmation prompts.
