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
| `GET/POST /jobs`, `GET /jobs/{id}` | Job list/create/read. |
| `POST /jobs/{id}/cancel`, `/retry` | Cancel or retry failed/cancelled work. |
| `POST /recipes/validate` | Validate schema and immutable service resource policy without rewriting the recipe. |
| `GET /capabilities` | Return providers plus effective recipe, upload, and request-body policy. |
| `GET /jobs/{id}/events` | Resumable SSE via `Last-Event-ID` or `after`. |
| `GET /jobs/{id}/events.json` | Return bounded normalized history; `tail=true` restores the newest window. |
| `GET /jobs/{id}/artifacts/{path}`, `/package.zip` | Re-hashed artifact and verified ZIP download. |

Every HTTP request body is spooled and counted before application parsing. A
non-multipart body is limited to 1 MiB by default; exact
`multipart/form-data` receives the configured raw-upload ceiling plus a 1 MiB
envelope allowance. Decimal `Content-Length` is checked before reading, and the
received byte count independently catches absent or dishonest lengths. Invalid
or duplicate lengths return 400; declared or observed overflow returns the
stable 413 detail `request exceeds configured limit`. After its one terminal
replay frame, the spool delegates to the underlying ASGI receive channel so
stream disconnect remains observable. Same-origin and trusted-host checks wrap
the spooler and reject hostile requests before buffering.

Upload then streams the parsed file to a private temporary path while hashing,
content-sniffs, and validates references before content-addressed storage. Since
web intake is one file, externally referenced glTF/OBJ members are rejected.
Inspection/preview parsing runs in bounded child processes and inspection
responses have a report-byte ceiling. The browser recipe is a small
server-expanded subset of the canonical strict Recipe. Expanded browser recipes
and submitted full recipes must remain inside the effective service policy.
Job views read metrics and validation only when the registered size is below the
evidence ceiling and the on-disk size and SHA-256 still match; corrupt evidence
is omitted rather than rendered as proof.

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
- Body spooling has byte bounds but no independent request-read deadline; the
  serving stack remains responsible for slow-client timeouts.
- SSE is SQLite-poll based rather than an external durable stream or WebSocket.
