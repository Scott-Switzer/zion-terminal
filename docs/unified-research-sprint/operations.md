# Remaining operator actions

The verified private Workers origin is `https://zion-financial-serving-v2-thin-staging.scswitzer.workers.dev`. The custom hostname remains a separate diagnostic gate. Existing permission and TLS evidence is retained in [the prior operator report](../sec-universe-sprint/operations.md); it is not a new success claim.

An account administrator must create a candidate narrowly scoped Basin SQL Read / Catalog Read / bucket-item read credential for the existing account and permitted public tables, or make an authorized token-management credential available locally through a mode-0600 file. Test SELECT and denied owned-namespace mutations against the actual Basin runtime before installing it. Current operator/OAuth provisioning returns 403/9109. The storage-read/write documentation discrepancy remains unresolved; neither broad write privileges nor the operator token belong in the serving runtime.

A zone administrator must inspect certificate coverage/status under scotttunnel.xyz → SSL/TLS → Edge Certificates and the existing Worker's Domains & Routes. Diagnostic access needs SSL certificates, Zone Settings and DNS reads. Prior probes failed before HTTP; privileged inspection returned 403. Preserve routing until the cause is established.

The 340 unsealed release directories and ambiguous legacy reference remain quarantined pending authoritative producer evidence. No fabricated manifests, licensed migration, new WRDS downloads or archive rewrites are authorized by this release.
