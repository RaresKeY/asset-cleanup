# Local web API

FastAPI exposes versioned routes beneath `/api/v1`; `/health/live` and
`/health/ready` are container probes. OpenAPI is `/api/v1/openapi.json` and
interactive documentation is disabled.

| Route family | Current contract |
|---|---|
| `GET/POST /workspaces` | List/create local workspaces. |
| `GET/POST /workspaces/{id}/sources` | List sources or stream one bounded upload. |
| `GET /workspaces/{id}/sources/{asset}/inspection` | Return bounded summary plus raw inspection. |
| `POST /workspaces/{id}/sources/{asset}/jobs` | Expand browser recipe and enqueue a job. |
| `GET /workspaces/{id}/jobs` | Return workspace-owned candidate threads, optionally filtered by `source_id`. |
| `POST /assets/import`, `GET /assets/{id}/preview` | Global import and verified derived GLB preview. |
| `GET /capabilities`, `GET /recipes/schema`, `POST /recipes/validate` | Report effective policy/capabilities, emit canonical schema, or validate schema and service policy. |
| `GET/POST /jobs`, `GET /jobs/{id}` | Job list/create/read. |
| `POST /jobs/{id}/cancel`, `/retry` | Cancel or retry failed/cancelled work. |
| `GET /jobs/{id}/events` | Resumable SSE via `Last-Event-ID` or `after`. |
| `GET /jobs/{id}/events.json` | Return bounded normalized history; `tail=true` restores the newest window. |
| `GET /jobs/{id}/artifacts/{path}`, `/package.zip` | Re-hashed artifact and verified ZIP download. |

Upload uses a bounded pre-multipart spool, then streams to a private temporary
file while hashing, content-sniffs, and validates references before
content-addressed storage. Since web intake is one file, externally referenced
glTF/OBJ members are rejected. Inspection/preview parsing runs in bounded child
processes and inspection responses have a report-byte ceiling. The browser recipe is a small server-expanded subset of the canonical strict
Recipe. New browser recipes request Khronos glTF validation by default and may
explicitly disable it. Legacy editor JSON that predates the field is accepted
only when a lossless projection from its immutable canonical recipe reproduces
the same canonical JSON and hash; retries persist that exact recovered editor
state. External validator warnings participate in the canonical
`fail_on_warning` gate.
Job views read metrics and validation only when the registered size is below the
evidence ceiling and the on-disk size and SHA-256 still match; corrupt evidence
is omitted rather than rendered as proof.

All HTTP request bodies, including bodies on otherwise read-only methods, are
bounded before FastAPI, multipart parsing, JSON decoding, or route code receives
them. The larger configured upload-plus-multipart-overhead allowance requires
all three of: method `POST`, an exact `multipart/form-data` media-type token,
and one of these exact upload paths:

- `/api/v1/assets/import`;
- the hidden compatibility alias `/api/v1/assets`;
- `/api/v1/workspaces/{workspace_id}/sources`, with a lowercase 32-hex workspace ID.

Every other method, path, or content type receives the smaller general request
allowance. Duplicate `Content-Type` fields are rejected before tier selection,
so conflicting headers cannot claim the multipart allowance for a non-upload
route. `Content-Length` is optional, but when supplied it must occur exactly
once and contain only decimal digits. Leading zeroes are accepted. Malformed,
duplicate, or declared over-limit values fail before body receipt, without
converting an arbitrarily long decimal to a Python integer. Streamed bytes are
independently counted, so a false or absent declaration cannot bypass the limit.

An accepted complete body is written to a bounded spooled temporary file and
replayed downstream in bounded chunks. A disconnect while the initial body is
still incomplete returns a 400 request-body error. Once replay finishes, later
receive calls delegate to the original channel so downstream disconnect state,
including SSE disconnect checks, is preserved.

The request order is same-origin browser check, trusted-host check, body
spool/limit, then FastAPI parsing and route handling. Cross-site or
untrusted-host requests are therefore rejected without first reading their
bodies.

Recipe validation, both job-creation routes, and retry validate the fully
expanded canonical recipe against the immutable server `RecipeCeilings`.
Violating an operator-owned resource maximum or evidence/complexity minimum
returns HTTP 422 with code `recipe_exceeds_server_policy` and path-ordered field-level
`requested` plus `maximum` or `minimum` facts. Values are rejected rather than
clamped, preserving the exact stored recipe, canonical hash, and eventual
execution provenance. `GET /capabilities` reports these effective recipe and
request-body policies. Its `service_policy.request_body` record contains
`max_upload_bytes`, `max_general_bytes`, `max_upload_multipart_bytes`, and the
three `multipart_upload_paths` allowed to use the larger upload allowance. The
`max_general_bytes` name is intentional: multipart requests outside those
routes also receive the general limit.

Workspace-created jobs retain their workspace identity. Public event records have
stable `sequence`, `created_utc`, `level`, `type`, `stage`, `status`,
`stage_status`, `progress`, `message`, and bounded `data`. The SSE and JSON event
routes serialize the same shape. Job views expose the latest durable event as
`event_sequence`, allowing clients to reject out-of-order polling responses.
Job views return `editor_recipe` when the validated compact input was stored or
when a legacy canonical recipe can be converted and expanded back without any
change. A canonical recipe outside the browser subset does not receive a
lossy editor projection.

## Gaps

- No authentication, authorization, deletion, bulk/bundle/resumable upload, or
  workspace/source pagination exists.
- SSE is SQLite-poll based rather than an external durable stream or WebSocket.
- Body limits bound request bytes, not response bandwidth or aggregate storage;
  rate limits and authenticated per-user quotas are not implemented.
