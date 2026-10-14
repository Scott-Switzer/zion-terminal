# Current operator actions

The authenticated native workers.dev service is working. These unresolved items do not block existing public SEC histories, screening or terminal access.

| Item | Evidence | Required human action |
|---|---|---|
| Least-privilege Basin runtime credential | Existing operator/OAuth token provisioning returned403/9109. The operator-only isolated SQL tests passed, but no narrow candidate was issued or runtime-certified. | Account administrator: in Cloudflare account51b88c6a6ef833b3c2ff46e98d5d9356, Manage Account → Account API Tokens → Create Token → Custom token. Start with SQL Read, Catalog Read and bucket-scoped item read for financial-system-datasets. Supply the candidate through a0600 local envfile, never chat. Verify an actual SELECT and denied mutations before installation. The SQL/Catalog documentation's storage-read/write discrepancy remains unresolved; do not broaden storage privileges based on assumption. |
| Custom-hostname TLS | Final unauthenticated curl probe still failed beforeHTTP withTLS protocol-version alert (exit35). Earlier SSL/certificate/DNS inspection returned403, so the cause is unconfirmed. | Zone administrator: scotttunnel.xyz → SSL/TLS → Edge Certificates: inspect certificate coverage/status; Workers & Pages → zion-financial-serving-v2-thin-staging → Settings → Domains & Routes: inspect custom-domain certificate status. Provide read access to SSL certificates, Zone Settings and DNS for diagnosis. Preserve current routing pending evidence. |
| Legacy archive recovery |340unsealed directories and one ambiguous reference remain quarantined. | Source producer: supply authoritative manifests and path evidence. Validate bounded footers/codecs/row census before any promotion. No invented manifests. |

Financial mapping work can continue independently. Seventy SEC source identity mismatches require authoritative source correction;5,190 issuers have no valid supported history. Annual debt coverage remains limited to one company; establish component nonoverlap from actual filings before adding mappings.

Keep original source archives and referenced immutable releases. No automatic archive garbage collection or WRDS collector was enabled. The sprint's$5 cap covers bounded incremental work and conservative first-month retained-storage reservations; it is not an account bill or authorization for an unbounded recurring refresh.
