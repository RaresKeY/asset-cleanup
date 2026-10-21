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

The multi-stage Dockerfile builds locked Node frontend assets and a locked
Python wheel. A separate disposable stage downloads the official Khronos glTF
Validator 2.0.0-dev.3.10 Linux-amd64 archive from its versioned release URL and
verifies SHA-256 before reading its members. Only the fixed native executable
and its complete upstream LICENSE/NOTICES enter the runtime image; archive,
Dart, Node, npm, and build tooling do not. The runtime uses Python 3.12 slim as
UID/GID 10001; `/data` is its writable volume. Non-amd64 builds fail explicitly
instead of silently omitting a requested validation gate. Compose publishes
loopback only, uses read-only root, noexec tmpfs, dropped capabilities,
no-new-privileges, PID/memory/CPU limits, and init. The secure overlay runs a
separate worker with `--no-embedded-worker`, the shared data volume, and
`network_mode: none`.

## Certificate trust

The project lock selects certifi 2026.10.19. Project resolver and build constraints prevent reintroducing affected pip, certifi, uv and sigstore versions. The shared Python base refreshes signed distro trust and verifies it before HTTPS, integrity-pins pip/certifi bootstrap wheels and repairs future venv seeds. Python build/runtime stages inherit that base and the final selected venv store is checked. The installer stage selects uv 0.13.4.

The existing local environment was repaired with reviewed offline pip/certifi wheels and passed effective-store, twelve-chain file/directory controls and ordinary/negative live TLS. This does not attest an existing deployment image. The legacy Node frontend build is outside the permitted execution workflow; no Node/npm command was run. Full container rebuild remains unverified pending a permitted frontend build path and corrected host container-registry client.

## Gaps

- Release provenance, SBOM/signing/scanning, rootless host guidance, TLS/reverse
  proxy, authentication, and production secrets policy need release documents.
- Local trusted-host and same-origin controls are not authentication for remote
  deployment.
- Per-job POSIX limits and container controls are defense in depth, but the
  current worker is not yet a per-job rootless OCI/gVisor sandbox with a private
  filesystem and mandatory no-network boundary.
- Validator artifact signing/reproducible builds and Linux arm64 packaging are
  absent; upstream advisory monitoring and SBOM generation remain manual.
