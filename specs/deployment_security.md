# Deployment and security

The local service defaults to loopback and explicit trusted hosts. It rejects
cross-origin browser writes, disables proxy headers, and emits CSP, `nosniff`,
no-referrer, and restrictive Permissions-Policy headers. Storage paths are
generated; preview/artifact routes use confined registered paths and re-hash
before serving.

All HTTP bodies are counted before FastAPI parsing. Non-multipart bodies default
to 1 MiB; multipart bodies receive only the configured raw-file limit plus a
1 MiB envelope allowance. Strict decimal declared lengths and actual streamed
bytes are both checked. Trusted-host and same-origin rejection happen outside
this spool boundary. Canonical recipes are rejected, never silently clamped,
when they exceed frozen service resource ceilings. The API checks validation,
creation, and retry; the worker repeats the check before any processing child is
spawned, covering direct database insertion and split-service policy mismatch.

The multi-stage Dockerfile builds locked Node frontend assets and a locked Python
wheel, then runs Python 3.12 slim as UID/GID 10001. Node/build tooling is absent
from runtime; `/data` is the writable volume. Compose publishes loopback only,
uses read-only root, noexec tmpfs, dropped capabilities, no-new-privileges,
PID/memory/CPU limits, and init. The secure overlay runs a separate worker with
`--no-embedded-worker`, the shared data volume, and `network_mode: none`.

## Gaps

- Release provenance, SBOM/signing/scanning, rootless host guidance, TLS/reverse
  proxy, authentication, and production secrets policy need release documents.
- Local trusted-host and same-origin controls are not authentication for remote
  deployment.
- Per-request read deadlines and aggregate concurrent spool/disk quotas still
  depend on the serving and container deployment layers.
