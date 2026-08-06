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
| `POST /assets/import`, `GET /assets/{id}/preview` | Global import and verified derived GLB preview. |
| `GET/POST /jobs`, `GET /jobs/{id}` | Job list/create/read. |
| `POST /jobs/{id}/cancel`, `/retry` | Cancel or retry failed/cancelled work. |
| `GET /jobs/{id}/events` | Resumable SSE via `Last-Event-ID` or `after`. |
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

## Gaps

- No authentication, authorization, deletion, bulk/bundle/resumable upload, or
  workspace/source pagination exists.
- SSE is SQLite-poll based rather than an external durable stream or WebSocket.
