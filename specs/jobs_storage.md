# Jobs, storage, and worker execution

`WebConfig.data_root` owns `state.sqlite3`, `assets/`, `jobs/`, and `tmp/`.
SQLite uses foreign keys, WAL, a busy timeout, and `BEGIN IMMEDIATE` transitions.
Assets are SHA-256-addressed and can attach to many workspaces. Jobs retain an
immutable recipe/hash, source, nullable workspace owner, output path, retry
parent, state/timestamps, cancellation/error facts, events, and artifact
path/kind/stage/size/hash. Workspace jobs and their retries preserve that owner;
the idempotent SQLite bootstrap migration assigns older jobs only when their
source belonged to exactly one workspace, avoiding ambiguous history.
Workspace jobs also retain the validated compact browser-editor input. For
older rows without that input, API reads derive it from the immutable canonical
recipe only when expanding the derived editor fields reproduces the canonical
recipe exactly. Unsupported full recipes remain visible but are not presented
as safely editable browser recipes; retry persists a successfully derived form.

The oldest un-cancelled queued job is atomically claimed. Worker startup changes
interrupted `running` jobs to `failed` with `WorkerInterrupted`; retry creates a
fresh linked job. Queued cancellation is immediate; running cancellation is a
flag observed by the worker.

Each job, and CLI parser work, runs in a child process group with disconnected stdin/stdout and a
private stderr log. The parent mirrors bounded JSONL events into SQLite, applies
the recipe wall-time limit, sends TERM/terminate, waits three seconds, then
sends KILL/kill if needed. POSIX children set CPU, address-space, and open-file
limits before mesh parsing. Failure registers only surviving confined artifacts.
Any monitoring/event-storage exception terminates the still-running child before
the job is failed. The worker rechecks the source's stored size and SHA-256 before
launch. Download and ZIP creation recheck registered file path, size, and SHA-256.
Lifecycle and mirrored pipeline events retain normalized stage, status,
stage-status, and progress fields so the SSE stream, JSON history, thread rail,
and copied console agree. Web jobs persist the exact compact editor input beside
the expanded canonical recipe. Legacy jobs expose editor settings only when a
round-trip projection reproduces that immutable canonical recipe exactly.
Summary reads use the newest bounded event window in chronological order.
Cursor reads retain forward pagination, and terminal SSE streams drain every
full backlog page before closing.

## Gaps

- There is one logical worker: multi-host leases, priorities, cleanup/retention,
  and object storage are not implemented.
- Non-POSIX parser isolation/limits, disk/output quotas, seccomp, and cgroup
  enforcement remain deployment work.
