import { type DragEvent, type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, subscribeJobEvents } from "./api";
import { RecipeEditor } from "./RecipeEditor";
import { SceneViewport, type CompareMode } from "./SceneViewport";
import {
  formatCount,
  formatRunLog,
  isActiveJob,
  jobProgressPercent,
  jobStage,
  mergeJobs,
  mergeRunEvents,
  preferNewerJob,
  upsertJob,
} from "./state";
import { defaultRecipe, type AssetSource, type Inspection, type Job, type RunEvent, type Workspace } from "./types";

function displayError(error: unknown): string {
  return error instanceof ApiError ? error.message : "Could not reach the local Asset Cleanup service.";
}

function metricName(key: string): string {
  return key.replaceAll("_", " ");
}

function evidenceRows(value: unknown, prefix = ""): Array<[string, string]> {
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
    return [[prefix, value === null ? "—" : String(value)]];
  }
  if (Array.isArray(value)) {
    return [[prefix, value.length > 4 ? `${value.length} values` : JSON.stringify(value)]];
  }
  if (typeof value !== "object") return [];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, nested]) =>
    evidenceRows(nested, prefix ? `${prefix}.${key}` : key),
  );
}

function StatusPill({ state }: { state: Job["state"] }) {
  return <span className={`status ${state}`}>{state}</span>;
}

function statusDotClass(state: Job["state"]): string {
  if (isActiveJob(state)) return "active";
  if (state === "accepted" || state === "candidate") return "complete";
  if (state === "failed" || state === "cancelled") return "error";
  return "idle";
}

function containsFiles(event: DragEvent<HTMLElement>): boolean {
  return event.dataTransfer.types.includes("Files");
}

function fallbackCopy(text: string): boolean {
  const input = document.createElement("textarea");
  input.value = text;
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  return copied;
}

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspacesLoaded, setWorkspacesLoaded] = useState(false);
  const [workspaceId, setWorkspaceId] = useState<string>();
  const [workspacePromptOpen, setWorkspacePromptOpen] = useState(false);
  const [workspaceName, setWorkspaceName] = useState("Untitled asset");
  const [pendingUpload, setPendingUpload] = useState<File>();
  const [sources, setSources] = useState<AssetSource[]>([]);
  const [source, setSource] = useState<AssetSource>();
  const [inspection, setInspection] = useState<Inspection>();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [job, setJob] = useState<Job>();
  const [recipe, setRecipe] = useState(defaultRecipe);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connection, setConnection] = useState<"checking" | "ready" | "offline">("checking");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [copiedJobId, setCopiedJobId] = useState<string>();
  const [compareMode, setCompareMode] = useState<CompareMode>("split");
  const [wireframe, setWireframe] = useState(false);
  const [showCollision, setShowCollision] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const workspaceDialogRef = useRef<HTMLFormElement>(null);
  const workspaceNameRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const selectionEpoch = useRef(0);
  const workspaceIdRef = useRef<string | undefined>(undefined);
  const sourceIdRef = useRef<string | undefined>(undefined);
  const jobIdRef = useRef<string | undefined>(undefined);
  const busyRef = useRef(false);
  const currentJobId = job?.id;
  const currentJobState = job?.state;

  useEffect(() => { workspaceIdRef.current = workspaceId; }, [workspaceId]);
  useEffect(() => { sourceIdRef.current = source?.id; }, [source?.id]);
  useEffect(() => { jobIdRef.current = job?.id; }, [job?.id]);
  useEffect(() => { busyRef.current = busy; }, [busy]);

  useEffect(() => {
    if (!workspacePromptOpen) return undefined;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    const focusName = window.requestAnimationFrame(() => workspaceNameRef.current?.focus());
    const handleDialogKey = (event: KeyboardEvent) => {
      const dialog = workspaceDialogRef.current;
      if (!dialog) return;
      if (event.key === "Escape" && workspaces.length > 0 && !busyRef.current) {
        event.preventDefault();
        setWorkspacePromptOpen(false);
        setPendingUpload(undefined);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...dialog.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])",
      )];
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleDialogKey);
    return () => {
      window.cancelAnimationFrame(focusName);
      document.removeEventListener("keydown", handleDialogKey);
      previouslyFocused?.focus();
    };
  }, [workspacePromptOpen, workspaces.length]);

  const refreshWorkspaces = useCallback(async () => {
    try {
      const found = await api.listWorkspaces();
      setWorkspaces(found);
      setWorkspaceId((chosen) => {
        const next = found.some((item) => item.id === chosen) ? chosen : found[0]?.id;
        workspaceIdRef.current = next;
        return next;
      });
      if (found.length === 0) setWorkspacePromptOpen(true);
    } finally {
      setWorkspacesLoaded(true);
    }
  }, []);

  useEffect(() => {
    api.health()
      .then(() => {
        setConnection("ready");
        return refreshWorkspaces();
      })
      .catch((reason: unknown) => {
        setConnection("offline");
        setError(displayError(reason));
      });
  }, [refreshWorkspaces]);

  useEffect(() => {
    let current = true;
    setSources([]);
    setSource(undefined);
    sourceIdRef.current = undefined;
    setRecipe(defaultRecipe);
    setInspection(undefined);
    setJobs([]);
    setJob(undefined);
    jobIdRef.current = undefined;
    setEvents([]);
    setCopiedJobId(undefined);
    if (workspaceId) {
      Promise.all([api.listSources(workspaceId), api.listWorkspaceJobs(workspaceId)])
        .then(([foundSources, foundJobs]) => {
          if (!current) return;
          setSources(foundSources);
          setJobs(foundJobs.items);
          const active = foundJobs.items.find((item) => isActiveJob(item.state));
          const selectedSource = (active
            ? foundSources.find((item) => item.id === active.source_id)
            : undefined) ?? foundSources[0];
          setSource(selectedSource);
          setJob(active);
          sourceIdRef.current = selectedSource?.id;
          jobIdRef.current = active?.id;
          if (active) setRecipe(active.editor_recipe ?? defaultRecipe);
        })
        .catch((reason: unknown) => {
          if (current) setError(displayError(reason));
        });
    }
    return () => { current = false; };
  }, [workspaceId]);

  useEffect(() => {
    let current = true;
    if (!source || !workspaceId) {
      setInspection(undefined);
      return () => { current = false; };
    }
    setInspection(source.inspection ?? undefined);
    api.inspectSource(workspaceId, source.id)
      .then((found) => { if (current) setInspection(found); })
      .catch(() => undefined);
    return () => { current = false; };
  }, [source, workspaceId]);

  useEffect(() => {
    let current = true;
    if (!currentJobId) return () => { current = false; };
    api.listJobEvents(currentJobId)
      .then((found) => {
        if (current) setEvents((previous) => mergeRunEvents(previous, found.items));
      })
      .catch(() => undefined);
    return () => { current = false; };
  }, [currentJobId, currentJobState]);

  useEffect(() => {
    if (!currentJobId || !isActiveJob(currentJobState)) return undefined;
    let current = true;
    const refresh = () => api.getJob(currentJobId)
      .then((found) => {
        if (!current) return;
        setJob((selected) => selected?.id === found.id ? preferNewerJob(selected, found) : selected);
        setJobs((previous) => upsertJob(previous, found));
      })
      .catch(() => undefined);
    const unsubscribe = subscribeJobEvents(
      currentJobId,
      (event) => {
        if (!current) return;
        setEvents((previous) => mergeRunEvents(previous, [event]));
        refresh();
      },
      () => undefined,
    );
    const interval = window.setInterval(refresh, 2500);
    return () => {
      current = false;
      unsubscribe();
      window.clearInterval(interval);
    };
  }, [currentJobId, currentJobState]);

  const activeJobKey = useMemo(
    () => jobs.filter((item) => isActiveJob(item.state)).map((item) => item.id).sort().join(","),
    [jobs],
  );
  useEffect(() => {
    if (!workspaceId || !activeJobKey) return undefined;
    let current = true;
    const refresh = () => api.listWorkspaceJobs(workspaceId)
      .then((found) => {
        if (!current) return;
        setJobs((previous) => mergeJobs(previous, found.items));
        setJob((selected) => selected
          ? (() => {
              const refreshed = found.items.find((item) => item.id === selected.id);
              return refreshed ? preferNewerJob(selected, refreshed) : selected;
            })()
          : selected);
      })
      .catch(() => undefined);
    const interval = window.setInterval(refresh, 3000);
    return () => {
      current = false;
      window.clearInterval(interval);
    };
  }, [activeJobKey, workspaceId]);

  const createWorkspace = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = workspaceName.trim();
    if (!name) return;
    setBusy(true);
    setError(undefined);
    try {
      const workspace = await api.createWorkspace(name);
      let added: AssetSource | undefined;
      try {
        added = pendingUpload ? await api.uploadSource(workspace.id, pendingUpload) : undefined;
      } finally {
        selectionEpoch.current += 1;
        workspaceIdRef.current = workspace.id;
        sourceIdRef.current = added?.id;
        jobIdRef.current = undefined;
        setWorkspaces((previous) => [workspace, ...previous.filter((item) => item.id !== workspace.id)]);
        setWorkspaceId(workspace.id);
        setWorkspacePromptOpen(false);
        setWorkspaceName("Untitled asset");
        setPendingUpload(undefined);
      }
      if (added) {
        setSources([added]);
        setSource(added);
        setJobs([]);
        setJob(undefined);
        setEvents([]);
      }
    } catch (reason) {
      setError(displayError(reason));
    } finally {
      setBusy(false);
    }
  };

  const openWorkspacePrompt = () => {
    setWorkspaceName("Untitled asset");
    setWorkspacePromptOpen(true);
  };

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!workspaceId) {
      setPendingUpload(file);
      setWorkspacePromptOpen(true);
      return;
    }
    setBusy(true);
    setError(undefined);
    const targetWorkspaceId = workspaceId;
    const targetEpoch = selectionEpoch.current;
    try {
      const added = await api.uploadSource(targetWorkspaceId, file);
      if (workspaceIdRef.current !== targetWorkspaceId) return;
      setSources((previous) => [added, ...previous.filter((item) => item.id !== added.id)]);
      if (selectionEpoch.current === targetEpoch) {
        sourceIdRef.current = added.id;
        jobIdRef.current = undefined;
        setSource(added);
        setJob(undefined);
        setEvents([]);
        setCopiedJobId(undefined);
      }
    } catch (reason) {
      if (workspaceIdRef.current === targetWorkspaceId && selectionEpoch.current === targetEpoch) {
        setError(displayError(reason));
      }
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const run = async () => {
    if (!workspaceId || !source) return;
    const targetWorkspaceId = workspaceId;
    const targetSourceId = source.id;
    const targetEpoch = selectionEpoch.current;
    setBusy(true);
    setError(undefined);
    try {
      const created = await api.createJob(targetWorkspaceId, targetSourceId, recipe);
      if (workspaceIdRef.current !== targetWorkspaceId) return;
      setJobs((previous) => upsertJob(previous, created));
      if (selectionEpoch.current === targetEpoch && sourceIdRef.current === targetSourceId) {
        jobIdRef.current = created.id;
        setJob(created);
        setEvents(created.events ?? []);
        setCopiedJobId(undefined);
      }
    } catch (reason) {
      if (workspaceIdRef.current === targetWorkspaceId && selectionEpoch.current === targetEpoch) {
        setError(displayError(reason));
      }
    } finally {
      setBusy(false);
    }
  };

  const runAction = async (action: "cancel" | "retry") => {
    if (!job) return;
    const targetJobId = job.id;
    const targetWorkspaceId = workspaceId;
    const targetEpoch = selectionEpoch.current;
    setBusy(true);
    setError(undefined);
    try {
      const updated = action === "cancel" ? await api.cancelJob(targetJobId) : await api.retryJob(targetJobId);
      if (workspaceIdRef.current !== targetWorkspaceId) return;
      setJobs((previous) => upsertJob(previous, updated));
      if (selectionEpoch.current === targetEpoch && jobIdRef.current === targetJobId) {
        jobIdRef.current = updated.id;
        setJob(updated);
        if (action === "retry") setEvents(updated.events ?? []);
        setCopiedJobId(undefined);
      }
    } catch (reason) {
      if (workspaceIdRef.current === targetWorkspaceId && selectionEpoch.current === targetEpoch) {
        setError(displayError(reason));
      }
    } finally {
      setBusy(false);
    }
  };

  const selectSource = (selected: AssetSource) => {
    selectionEpoch.current += 1;
    sourceIdRef.current = selected.id;
    jobIdRef.current = undefined;
    setSource(selected);
    setJob(undefined);
    setEvents([]);
    setCopiedJobId(undefined);
  };

  const selectJob = (selected: Job) => {
    selectionEpoch.current += 1;
    jobIdRef.current = selected.id;
    setJob(selected);
    setEvents([]);
    setCopiedJobId(undefined);
    const selectedSource = sources.find((item) => item.id === selected.source_id);
    if (selectedSource) {
      sourceIdRef.current = selectedSource.id;
      setSource(selectedSource);
    }
    setRecipe(selected.editor_recipe ?? defaultRecipe);
  };

  const handleDragEnter = (event: DragEvent<HTMLElement>) => {
    if (!containsFiles(event)) return;
    event.preventDefault();
    if (busy) return;
    dragDepth.current += 1;
    setDraggingFiles(true);
  };
  const handleDragOver = (event: DragEvent<HTMLElement>) => {
    if (!containsFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = busy ? "none" : "copy";
  };
  const handleDragLeave = (event: DragEvent<HTMLElement>) => {
    if (dragDepth.current === 0) return;
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDraggingFiles(false);
  };
  const handleDrop = (event: DragEvent<HTMLElement>) => {
    if (event.dataTransfer.files.length === 0) return;
    event.preventDefault();
    dragDepth.current = 0;
    setDraggingFiles(false);
    if (!busy) void upload(event.dataTransfer.files[0]);
  };

  const selectWorkspace = (selectedWorkspaceId: string) => {
    selectionEpoch.current += 1;
    workspaceIdRef.current = selectedWorkspaceId;
    sourceIdRef.current = undefined;
    jobIdRef.current = undefined;
    setWorkspaceId(selectedWorkspaceId);
  };

  const sourceUrl = job?.source_preview_url
    ?? (source ? source.preview_url ?? api.sourcePreviewUrl(source.id) : undefined);
  const candidateUrl = job?.candidate_preview_url ?? undefined;
  const collisionUrl = job?.collision_preview_url ?? undefined;
  const statistics = inspection?.topology;
  const sourceNames = useMemo(() => new Map(sources.map((item) => [item.id, item.name])), [sources]);
  const currentSourceName = source?.name ?? (job?.source_id ? sourceNames.get(job.source_id) : undefined) ?? "Unknown source";
  const activityLog = useMemo(
    () => job ? formatRunLog(job, currentSourceName, events) : "",
    [currentSourceName, events, job],
  );
  const resultRows = useMemo(
    () => [
      ...evidenceRows(job?.metrics),
      ...evidenceRows(job?.validation, "validation"),
    ].filter(([name]) => name && !name.includes(".raw.")).slice(0, 24),
    [job?.metrics, job?.validation],
  );
  const activeJobCount = jobs.filter((item) => isActiveJob(item.state)).length;
  const viewportTitle = job ? `${currentSourceName} · candidate ${job.id.slice(0, 8)}` : source?.name ?? "No source selected";

  const copyActivityLog = async () => {
    if (!job || !activityLog) return;
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(activityLog);
      else if (!fallbackCopy(activityLog)) throw new Error("copy failed");
    } catch {
      if (!fallbackCopy(activityLog)) {
        setError("Could not copy the activity log. Select the console text and copy it manually.");
        return;
      }
    }
    setCopiedJobId(job.id);
    window.setTimeout(() => setCopiedJobId((current) => current === job.id ? undefined : current), 1800);
  };

  return (
    <main
      className={`app-shell ${draggingFiles ? "dragging-files" : ""}`}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <header className="topbar">
        <a className="brand" href="/"><span className="brand-mark">A</span><span>asset<span>cleanup</span></span></a>
        <div className="connection"><i className={connection} /> {connection === "ready" ? "Local service connected" : connection === "checking" ? "Connecting…" : "Service offline"}</div>
        <button className="quiet-button" onClick={openWorkspacePrompt} disabled={connection !== "ready" || busy}>＋ New workspace</button>
      </header>

      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError(undefined)} aria-label="Dismiss">×</button></div>}
      {draggingFiles && <div className="drop-overlay" role="status"><strong>Drop asset to import</strong><span>GLB, glTF, OBJ, PLY, or STL</span></div>}
      {workspacePromptOpen && (
        <div className="workspace-prompt-backdrop">
          <form ref={workspaceDialogRef} className="workspace-prompt" role="dialog" aria-modal="true" aria-labelledby="workspace-prompt-title" aria-describedby="workspace-prompt-description" onSubmit={createWorkspace}>
            <p className="eyebrow">First step</p>
            <h2 id="workspace-prompt-title">Create an asset workspace</h2>
            <p id="workspace-prompt-description">A workspace keeps imported sources and every candidate thread together.</p>
            {pendingUpload && <p className="pending-upload">Ready to import <strong>{pendingUpload.name}</strong> after creation.</p>}
            <label htmlFor="workspace-name">Workspace name</label>
            <input ref={workspaceNameRef} id="workspace-name" maxLength={128} value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} />
            <div className="workspace-prompt-actions">
              {workspaces.length > 0 && <button type="button" className="quiet-button" onClick={() => { setWorkspacePromptOpen(false); setPendingUpload(undefined); }}>Cancel</button>}
              <button type="submit" className="run-button" disabled={busy || connection !== "ready" || !workspaceName.trim()}>{busy ? "Creating…" : "Create workspace"}</button>
            </div>
          </form>
        </div>
      )}

      <div className="workbench">
        <aside className="left-rail">
          <div className="rail-title">
            <p className="eyebrow">Workspace</p>
            <select aria-label="Current workspace" value={workspaceId ?? ""} onChange={(event) => selectWorkspace(event.target.value)}>
              <option value="" disabled>{workspacesLoaded && workspaces.length === 0 ? "Create a workspace" : "Select workspace"}</option>
              {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
            </select>
          </div>

          <div className="source-list">
            <div className="section-label"><span>Sources</span><button aria-label="Upload source" onClick={() => inputRef.current?.click()} disabled={!workspaceId || busy}>＋</button></div>
            {sources.map((item) => (
              <button
                className={`source-item ${!job && source?.id === item.id ? "selected" : ""}`}
                key={item.id}
                aria-current={!job && source?.id === item.id ? "page" : undefined}
                onClick={() => selectSource(item)}
              >
                <span className="asset-icon">◇</span>
                <span><strong>{item.name}</strong><small>{item.kind ?? "asset"}</small></span>
              </button>
            ))}
            {sources.length === 0 && <p className="empty-list">{workspaceId ? "No source assets yet." : "Create a workspace to import assets."}</p>}
          </div>

          <div className="thread-list">
            <div className="section-label"><span>Candidate threads</span>{activeJobCount > 0 && <small>{activeJobCount} active</small>}</div>
            {jobs.map((item) => {
              const progress = jobProgressPercent(item);
              const selected = job?.id === item.id;
              return (
                <button
                  className={`thread-item ${selected ? "selected" : ""}`}
                  key={item.id}
                  aria-current={selected ? "page" : undefined}
                  onClick={() => selectJob(item)}
                >
                  <span className="thread-copy">
                    <strong>Candidate {item.id.slice(0, 8)}</strong>
                    <small>{sourceNames.get(item.source_id ?? "") ?? "Unknown source"} · {isActiveJob(item.state) ? jobStage(item) : item.state}</small>
                  </span>
                  <span className="thread-trailing">
                    <span className={`thread-status-dot ${statusDotClass(item.state)}`} aria-label={item.state} />
                    <small>{isActiveJob(item.state) ? `${progress}%` : item.state}</small>
                    <span className="thread-progress" role="progressbar" aria-label={`Candidate ${item.id.slice(0, 8)} progress`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
                      <i style={{ width: `${progress}%` }} />
                    </span>
                  </span>
                </button>
              );
            })}
            {jobs.length === 0 && <p className="empty-list">Built candidates appear here and remain selectable.</p>}
          </div>

          <div className="upload-card">
            <strong>Import source</strong>
            <p>Drop an asset anywhere, or choose a self-contained GLB/glTF, OBJ, PLY, or STL.</p>
            <button className="secondary-button" onClick={() => inputRef.current?.click()} disabled={!workspaceId || busy}>Choose file</button>
            <input ref={inputRef} type="file" accept=".glb,.gltf,.obj,.ply,.stl,model/gltf-binary,model/gltf+json" onChange={(event) => void upload(event.target.files?.[0])} hidden />
          </div>
        </aside>

        <section className="main-column">
          <div className="viewport-toolbar">
            <div><p className="eyebrow">{job ? "Candidate thread" : "Source"}</p><h1>{viewportTitle}</h1></div>
            <div className="toolbar-controls">
              <div className="segmented" aria-label="Comparison mode">
                <button className={compareMode === "split" ? "active" : ""} onClick={() => setCompareMode("split")}>Split</button>
                <button className={compareMode === "overlay" ? "active" : ""} onClick={() => setCompareMode("overlay")}>Overlay</button>
              </div>
              <label className="mini-toggle"><input type="checkbox" checked={wireframe} onChange={(event) => setWireframe(event.target.checked)} /> Wireframe</label>
              <label className="mini-toggle"><input type="checkbox" checked={showCollision} onChange={(event) => setShowCollision(event.target.checked)} disabled={!collisionUrl} /> Collision</label>
            </div>
          </div>
          <SceneViewport sourceUrl={sourceUrl} candidateUrl={candidateUrl} collisionUrl={collisionUrl} mode={compareMode} wireframe={wireframe} showCollision={showCollision} />

          <section className="activity">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Activity console</p>
                <h2>{job ? `${isActiveJob(job.state) ? "Running" : job.state} · ${jobStage(job)} · ${jobProgressPercent(job)}%` : "No candidate selected"}</h2>
                {job && <p className="activity-context"><code>{job.id}</code><span>{currentSourceName}</span>{job.message && <span>{job.message}</span>}</p>}
              </div>
              {job && (
                <div className="job-actions">
                  <StatusPill state={job.state} />
                  {isActiveJob(job.state) && <button className="danger-button" onClick={() => void runAction("cancel")} disabled={busy || job.cancel_requested}>{job.cancel_requested ? "Stopping…" : "Cancel"}</button>}
                  {["failed", "cancelled"].includes(job.state) && <button className="secondary-button" onClick={() => void runAction("retry")} disabled={busy}>Retry</button>}
                </div>
              )}
            </div>
            {job && <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={jobProgressPercent(job)}><div style={{ width: `${jobProgressPercent(job)}%` }} /></div>}
            {job ? (
              <>
                <div className="console-toolbar"><span>{events.length} {events.length === 1 ? "event" : "events"}</span><button className="quiet-button" onClick={() => void copyActivityLog()} aria-live="polite" aria-label="Copy complete job activity log">{copiedJobId === job.id ? "Copied" : "Copy log"}</button></div>
                <pre className="console-output" tabIndex={0} aria-label="Copyable plain-text job activity">{activityLog}</pre>
              </>
            ) : <p className="empty-activity">Build a candidate or select a candidate thread to inspect its exact job, stage, status, and console events.</p>}
          </section>
        </section>

        <aside className="right-rail">
          {job && (
            <p className={`recipe-context ${job.editor_recipe ? "restored" : "unavailable"}`}>
              {job.editor_recipe
                ? `New-candidate settings restored from ${job.id.slice(0, 8)}.`
                : "This thread used a canonical recipe the browser cannot represent exactly. The defaults below apply only to a new candidate; the stored run is unchanged."}
            </p>
          )}
          <RecipeEditor recipe={recipe} setRecipe={setRecipe} />
          <button className="run-button" onClick={() => void run()} disabled={!source || !workspaceId || busy || isActiveJob(job?.state)}>{busy ? "Working…" : job && isActiveJob(job.state) ? "Run in progress" : "Build candidate"}</button>
          <section className="panel inspector">
            <div className="panel-heading"><div><p className="eyebrow">Evidence</p><h2>Inspection</h2></div></div>
            <div className="metric-grid"><div><small>Vertices</small><strong>{formatCount(statistics?.vertices)}</strong></div><div><small>Faces</small><strong>{formatCount(statistics?.faces)}</strong></div><div><small>Components</small><strong>{formatCount(statistics?.components)}</strong></div><div><small>Watertight</small><strong>{statistics?.watertight === undefined ? "—" : statistics.watertight ? "Yes" : "No"}</strong></div></div>
            {inspection?.primitive_evidence?.length ? <div className="shape-list"><h3>Detected shape evidence</h3>{inspection.primitive_evidence.map((item) => <div key={`${item.kind}-${item.summary}`}><span>{item.kind}</span><meter min="0" max="1" value={item.confidence} /><small>{Math.round(item.confidence * 100)}%</small></div>)}</div> : <p className="muted">Select a source to inspect topology, scene inventory, and geometry evidence.</p>}
            {inspection?.warnings?.map((warning) => <p className="inline-warning" key={warning}>⚠ {warning}</p>)}
          </section>
          <section className="panel inspector">
            <div className="panel-heading"><div><p className="eyebrow">Validation</p><h2>Candidate result</h2></div>{job && <StatusPill state={job.state} />}</div>
            {resultRows.length ? <div className="result-list">{resultRows.map(([key, value]) => <div key={key}><span>{metricName(key)}</span><strong>{value}</strong></div>)}</div> : <p className="muted">Metrics appear when the candidate reaches validation.</p>}
            {job?.artifacts?.length ? <div className="artifacts"><h3>Package artifacts</h3>{job.artifacts.map((artifact) => <a key={artifact.name} href={artifact.url ?? api.artifactUrl(job.id, artifact.name)} download>{artifact.name}<span>↓</span></a>)}</div> : null}
          </section>
        </aside>
      </div>
    </main>
  );
}
