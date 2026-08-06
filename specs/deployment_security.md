# Deployment and security

The local service defaults to loopback and explicit trusted hosts. It rejects
cross-origin browser writes, disables proxy headers, and emits CSP, `nosniff`,
no-referrer, and restrictive Permissions-Policy headers. Storage paths are
generated; preview/artifact routes use confined registered paths and re-hash
before serving.

After the outer same-origin and trusted-host checks, the request-body boundary
validates `Content-Length`, applies separate general and multipart byte
ceilings, counts the received stream, and spools/replays accepted bodies before
framework parsing. The larger multipart tier requires `POST`, an exact
`multipart/form-data` media type, and an exact global or workspace upload path;
all other requests stay under the general limit. Duplicate `Content-Type`
fields fail before tier selection. Host or origin rejection therefore does not
first consume an attacker's body, while an accepted-origin body still cannot
reach an application parser unbounded. The service also owns an immutable
`RecipeCeilings` policy with resource maximums and shape-evidence/complexity
minimums. API validation, creation, and retry paths reject recipes outside it,
and the worker rechecks stored recipes before starting a processing child so
stale, imported, or directly modified queue state cannot bypass operator policy.

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
- Per-job POSIX limits and container controls are defense in depth, but the
  current worker is not yet a per-job rootless OCI/gVisor sandbox with a private
  filesystem and mandatory no-network boundary.
