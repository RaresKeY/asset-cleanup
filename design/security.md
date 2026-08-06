# Security design

Mesh and image inputs are hostile data. A standalone local application still
needs bounded parsing and execution because malformed geometry, recursive scene
graphs, external URIs, archives, textures, and native processors can exhaust or
escape the service.

## Intake boundaries

- Stream uploads to disk while hashing; never buffer an unbounded upload.
- Treat names as display metadata and generate all storage paths internally.
- Reject absolute paths, `..`, symlinks, device entries, nested archives,
  excessive entry counts, suspicious compression ratios, and expanded-size
  violations.
- Confine glTF/OBJ dependencies to the imported bundle. Reject network/file URIs
  and bound data URIs.
- Preflight container headers and declared counts before full parsing; enforce
  scene depth, vertex, face, accessor, image dimension, texture-pixel, file,
  memory, and wall-time limits.
- Classify PLY contents rather than trusting the suffix.
- Never generically unpickle or `torch.load` an unknown TRELLIS `.bin`. A retained
  capture is accepted only through a pinned provider contract in an isolated,
  explicitly enabled adapter.

## Execution boundaries

- Recipes contain only versioned typed data and a small declarative condition
  vocabulary—no Python, templates, shell, or arbitrary executable paths.
- The long-lived service owns immutable recipe ceilings. Bound both ordinary
  resource maximums and minimum evidence/complexity thresholds. Post-fit sample
  and cylinder-bin floors reject weak primitive evidence; the planar-area floor
  chiefly bounds report cardinality. Reject rather than silently clamp so the
  canonical recipe and hash remain truthful. Validate on schema/policy
  validation, create, retry, and immediately before worker execution.
- External adapters receive fixed executable identities and argument arrays.
- Bundled executable providers come only from fixed upstream release identities.
  Verify an owned cryptographic digest before reading archive members, retain
  upstream license/notice material, copy only required runtime files, and fail
  unsupported architectures explicitly. Release archives remain hostile until
  verified; build-time network access must not become runtime network access.
- Jobs run in separate process groups; the current POSIX child enforces CPU,
  address-space, open-file, and time limits, while Compose/container limits add
  process/memory/CPU boundaries.
- Cancellation terminates the process group and records whether escalation was
  needed.
- Stage output is written under an isolated staging directory, validated,
  hashed, and atomically promoted.
- A native crash or out-of-memory exit fails the job without corrupting the API
  database or an accepted candidate.

## Web and container boundaries

- Same-origin access and loopback binding are defaults; CORS is disabled.
- Bound every request body before framework parsers. Validate strict
  `Content-Length` syntax and duplicates as an early rejection only; streamed
  byte counting remains authoritative when the header is absent or dishonest.
  Spool accepted bodies to a private bounded temporary file and replay them in
  bounded chunks so multipart and JSON parsing share one memory-safe boundary.
- Grant the larger multipart allowance only to exact upload methods and routes;
  a content-type claim alone must never enlarge another endpoint's body budget.
- Reject invalid origins and hosts outside the spooling boundary so an attacker
  cannot force the service to consume its full body allowance before rejection.
- Inline rendering is limited to sanitized derived preview GLB and approved
  images. Raw uploads download as attachments.
- Artifact access resolves registered immutable IDs beneath the data root.
- The image runs non-root; Compose drops capabilities, uses read-only root and
  noexec tmpfs, and never mounts a container-engine socket.
- A stronger deployment separates API and worker; the worker can run without a
  network while sharing only the data volume.
- The first release is intentionally unauthenticated and local-only. Remote
  exposure requires an authenticated reverse proxy.

## Gaps

- Platform-specific sandboxing beyond POSIX resource limits needs a separate
  Linux/macOS/Windows design.
- Fuzz corpora and decompression-bomb fixtures are not yet assembled.
- Remote multi-user authorization, quotas, and audit policy are deferred.
- Aggregate request-rate, response-bandwidth, temporary-disk, and workspace
  storage budgets still need an authenticated deployment design.
- Validator artifact signing/reproducible builds, automated upstream
  advisory/issue monitoring, and a verified Linux arm64 provider remain open
  supply-chain work.
