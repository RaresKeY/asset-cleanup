import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, subscribeJobEvents } from "./api";
import { RecipeEditor } from "./RecipeEditor";
import { SceneViewport, type CompareMode } from "./SceneViewport";
import { formatCount, isActiveJob } from "./state";
import { defaultRecipe, type AssetSource, type Inspection, type Job, type RunEvent, type Workspace } from "./types";

function displayError(error: unknown): string { return error instanceof ApiError ? error.message : "Could not reach the local Asset Cleanup service."; }
function metricName(key: string): string { return key.replaceAll("_", " "); }
function progressPercent(value: number | undefined, active: boolean): number {
  if (value === undefined) return active ? 8 : 100;
  return Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
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
function StatusPill({ state }: { state: Job["state"] }) { return <span className={`status ${state}`}>{state}</span>; }

export default function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState<string>();
  const [sources, setSources] = useState<AssetSource[]>([]);
  const [source, setSource] = useState<AssetSource>();
  const [inspection, setInspection] = useState<Inspection>();
  const [job, setJob] = useState<Job>();
  const [recipe, setRecipe] = useState(defaultRecipe);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connection, setConnection] = useState<"checking" | "ready" | "offline">("checking");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [compareMode, setCompareMode] = useState<CompareMode>("split");
  const [wireframe, setWireframe] = useState(false);
  const [showCollision, setShowCollision] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const currentJobId = job?.id;
  const currentJobState = job?.state;

  const refreshWorkspaces = useCallback(async () => {
    const found = await api.listWorkspaces();
    setWorkspaces(found);
    setWorkspaceId((chosen) => chosen ?? found[0]?.id);
  }, []);
  useEffect(() => { api.health().then(() => { setConnection("ready"); return refreshWorkspaces(); }).catch(() => setConnection("offline")); }, [refreshWorkspaces]);
  useEffect(() => {
    let current = true;
    setSources([]); setSource(undefined); setInspection(undefined); setJob(undefined); setEvents([]);
    if (workspaceId) {
      api.listSources(workspaceId)
        .then((found) => { if (current) setSources(found); })
        .catch((reason: unknown) => { if (current) setError(displayError(reason)); });
    }
    return () => { current = false; };
  }, [workspaceId]);
  useEffect(() => {
    let current = true;
    if (!source || !workspaceId) { setInspection(undefined); return () => { current = false; }; }
    setInspection(source.inspection);
    api.inspectSource(workspaceId, source.id)
      .then((found) => { if (current) setInspection(found); })
      .catch(() => undefined);
    return () => { current = false; };
  }, [source, workspaceId]);
  useEffect(() => {
    if (!currentJobId || !isActiveJob(currentJobState)) return undefined;
    const refresh = () => api.getJob(currentJobId).then(setJob).catch(() => undefined);
    const unsubscribe = subscribeJobEvents(currentJobId, (event) => { setEvents((previous) => [...previous, event]); refresh(); }, () => undefined);
    const interval = window.setInterval(refresh, 2500);
    return () => { unsubscribe(); window.clearInterval(interval); };
  }, [currentJobId, currentJobState]);

  const createWorkspace = async () => {
    const name = window.prompt("Workspace name", "Untitled asset");
    if (!name?.trim()) return;
    setBusy(true); setError(undefined);
    try { const workspace = await api.createWorkspace(name.trim()); setWorkspaces((previous) => [workspace, ...previous]); setWorkspaceId(workspace.id); } catch (reason) { setError(displayError(reason)); } finally { setBusy(false); }
  };
  const upload = async (file: File | undefined) => {
    if (!file || !workspaceId) return;
    setBusy(true); setError(undefined);
    try { const added = await api.uploadSource(workspaceId, file); setSources((previous) => [added, ...previous]); setSource(added); setJob(undefined); setEvents([]); } catch (reason) { setError(displayError(reason)); } finally { setBusy(false); if (inputRef.current) inputRef.current.value = ""; }
  };
  const run = async () => {
    if (!workspaceId || !source) return;
    setBusy(true); setError(undefined);
    try { const created = await api.createJob(workspaceId, source.id, recipe); setJob(created); setEvents(created.events ?? []); } catch (reason) { setError(displayError(reason)); } finally { setBusy(false); }
  };
  const runAction = async (action: "cancel" | "retry") => { if (!job) return; setBusy(true); try { setJob(action === "cancel" ? await api.cancelJob(job.id) : await api.retryJob(job.id)); } catch (reason) { setError(displayError(reason)); } finally { setBusy(false); } };
  const sourceUrl = job?.source_preview_url ?? source?.preview_url;
  const candidateUrl = job?.candidate_preview_url;
  const collisionUrl = job?.collision_preview_url;
  const statistics = inspection?.topology;
  const eventRows = useMemo(() => [...events].slice(-50).reverse(), [events]);
  const resultRows = useMemo(
    () => [
      ...evidenceRows(job?.metrics),
      ...evidenceRows(job?.validation, "validation"),
    ].filter(([name]) => name && !name.includes(".raw.")).slice(0, 24),
    [job?.metrics, job?.validation],
  );

  return <main className="app-shell">
    <header className="topbar"><a className="brand" href="/"><span className="brand-mark">A</span><span>asset<span>cleanup</span></span></a><div className="connection"><i className={connection} /> {connection === "ready" ? "Local service connected" : connection === "checking" ? "Connecting…" : "Service offline"}</div><button className="quiet-button" onClick={createWorkspace} disabled={connection !== "ready" || busy}>＋ New workspace</button></header>
    {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError(undefined)} aria-label="Dismiss">×</button></div>}
    <div className="workbench">
      <aside className="left-rail"><div className="rail-title"><p className="eyebrow">Workspace</p><select aria-label="Current workspace" value={workspaceId ?? ""} onChange={(event) => setWorkspaceId(event.target.value)}><option value="" disabled>Select workspace</option>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}</select></div>
        <div className="source-list"><div className="section-label"><span>Sources</span><button aria-label="Upload source" onClick={() => inputRef.current?.click()} disabled={!workspaceId || busy}>＋</button></div>{sources.map((item) => <button className={`source-item ${source?.id === item.id ? "selected" : ""}`} key={item.id} onClick={() => { setSource(item); setJob(undefined); setEvents([]); }}><span className="asset-icon">◇</span><span><strong>{item.name}</strong><small>{item.kind ?? "asset"}</small></span></button>)}{sources.length === 0 && <p className="empty-list">No source assets yet.</p>}</div>
        <div className="upload-card"><strong>Import source</strong><p>Self-contained GLB or embedded glTF; geometry-only OBJ, PLY, and STL. Multi-file bundles remain available through the CLI.</p><button className="secondary-button" onClick={() => inputRef.current?.click()} disabled={!workspaceId || busy}>Choose file</button><input ref={inputRef} type="file" accept=".glb,.gltf,.obj,.ply,.stl,model/gltf-binary,model/gltf+json" onChange={(event) => upload(event.target.files?.[0])} hidden /></div>
      </aside>
      <section className="main-column"><div className="viewport-toolbar"><div><p className="eyebrow">Comparison</p><h1>{source?.name ?? "No source selected"}</h1></div><div className="toolbar-controls"><div className="segmented" aria-label="Comparison mode"><button className={compareMode === "split" ? "active" : ""} onClick={() => setCompareMode("split")}>Split</button><button className={compareMode === "overlay" ? "active" : ""} onClick={() => setCompareMode("overlay")}>Overlay</button></div><label className="mini-toggle"><input type="checkbox" checked={wireframe} onChange={(event) => setWireframe(event.target.checked)} /> Wireframe</label><label className="mini-toggle"><input type="checkbox" checked={showCollision} onChange={(event) => setShowCollision(event.target.checked)} disabled={!collisionUrl} /> Collision</label></div></div>
        <SceneViewport sourceUrl={sourceUrl} candidateUrl={candidateUrl} collisionUrl={collisionUrl} mode={compareMode} wireframe={wireframe} showCollision={showCollision} />
        <section className="activity"><div className="panel-heading"><div><p className="eyebrow">Activity</p><h2>{job ? <>Run <code>{job.id.slice(0, 8)}</code></> : "No candidate running"}</h2></div>{job && <div className="job-actions"><StatusPill state={job.state} />{isActiveJob(job.state) && <button className="danger-button" onClick={() => runAction("cancel")} disabled={busy || job.cancel_requested}>{job.cancel_requested ? "Stopping…" : "Cancel"}</button>}{["failed", "cancelled"].includes(job.state) && <button className="secondary-button" onClick={() => runAction("retry")} disabled={busy}>Retry</button>}</div>}</div>{job && <div className="progress"><div style={{ width: `${progressPercent(job.progress, isActiveJob(job.state))}%` }} /></div>}<div className="events">{eventRows.length ? eventRows.map((event) => <div className={`event ${event.level}`} key={`${event.sequence}-${event.created_utc}`}><time>{new Date(event.created_utc).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time><span>{event.stage ?? event.type}</span><p>{event.message}</p></div>) : <p className="empty-activity">Run a recipe to see stage events, warnings, timings, and validation evidence.</p>}</div></section>
      </section>
      <aside className="right-rail"><RecipeEditor recipe={recipe} setRecipe={setRecipe} /><button className="run-button" onClick={run} disabled={!source || !workspaceId || busy || isActiveJob(job?.state)}>{busy ? "Working…" : job && isActiveJob(job.state) ? "Run in progress" : "Build candidate"}</button>
        <section className="panel inspector"><div className="panel-heading"><div><p className="eyebrow">Evidence</p><h2>Inspection</h2></div></div><div className="metric-grid"><div><small>Vertices</small><strong>{formatCount(statistics?.vertices)}</strong></div><div><small>Faces</small><strong>{formatCount(statistics?.faces)}</strong></div><div><small>Components</small><strong>{formatCount(statistics?.components)}</strong></div><div><small>Watertight</small><strong>{statistics?.watertight === undefined ? "—" : statistics.watertight ? "Yes" : "No"}</strong></div></div>{inspection?.primitive_evidence?.length ? <div className="shape-list"><h3>Detected shape evidence</h3>{inspection.primitive_evidence.map((item) => <div key={`${item.kind}-${item.summary}`}><span>{item.kind}</span><meter min="0" max="1" value={item.confidence} /><small>{Math.round(item.confidence * 100)}%</small></div>)}</div> : <p className="muted">Select a source to inspect topology, scene inventory, and geometry evidence.</p>}{inspection?.warnings?.map((warning) => <p className="inline-warning" key={warning}>⚠ {warning}</p>)}</section>
        <section className="panel inspector"><div className="panel-heading"><div><p className="eyebrow">Validation</p><h2>Candidate result</h2></div>{job && <StatusPill state={job.state} />}</div>{resultRows.length ? <div className="result-list">{resultRows.map(([key, value]) => <div key={key}><span>{metricName(key)}</span><strong>{value}</strong></div>)}</div> : <p className="muted">Metrics appear when the candidate reaches validation.</p>}{job?.artifacts?.length ? <div className="artifacts"><h3>Package artifacts</h3>{job.artifacts.map((artifact) => <a key={artifact.name} href={artifact.url ?? api.artifactUrl(job.id, artifact.name)} download>{artifact.name}<span>↓</span></a>)}</div> : null}</section>
      </aside>
    </div>
  </main>;
}
