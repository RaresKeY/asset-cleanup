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
- External adapters receive fixed executable identities and argument arrays.
- Jobs run in separate process groups with bounded CPU, address space, files,
  processes, output bytes, and time where the platform supports them.
- Cancellation terminates the process group and records whether escalation was
  needed.
- Stage output is written under an isolated staging directory, validated,
  hashed, and atomically promoted.
- A native crash or out-of-memory exit fails the job without corrupting the API
  database or an accepted candidate.

## Web and container boundaries

- Same-origin access and loopback binding are defaults; CORS is disabled.
- Inline rendering is limited to sanitized derived preview GLB and approved
  images. Raw uploads download as attachments.
- Artifact access resolves registered immutable IDs beneath the data root.
- The image runs non-root, drops capabilities, supports a read-only root
  filesystem, and never mounts a container-engine socket.
- A stronger deployment separates API and worker; the worker can run without a
  network while sharing only the data volume.
- The first release is intentionally unauthenticated and local-only. Remote
  exposure requires an authenticated reverse proxy.

## Gaps

- Platform-specific sandboxing beyond POSIX resource limits needs a separate
  Linux/macOS/Windows design.
- Fuzz corpora and decompression-bomb fixtures are not yet assembled.
- Remote multi-user authorization, quotas, and audit policy are deferred.

