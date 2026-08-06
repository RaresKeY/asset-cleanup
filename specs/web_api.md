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
| `GET /jobs/{id}/events` | Resumable SSE via `Last-Event-ID` or `after`. |
| `GET /jobs/{id}/events.json` | Return bounded normalized history; `tail=true` restores the newest window. |
| `GET /jobs/{id}/artifacts/{path}`, `/package.zip` | Re-hashed artifact and verified ZIP download. |

Upload uses a bounded pre-multipart spool, then streams to a private temporary
file while hashing, content-sniffs, and validates references before
content-addressed storage. Since web intake is one file, externally referenced
glTF/OBJ members are rejected. Inspection/preview parsing runs in bounded child
processes and inspection responses have a report-byte ceiling. The browser
recipe is a small server-expanded subset of the canonical strict Recipe.
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
- SSE is SQLite-poll based rather than an external durable stream or WebSocket.
