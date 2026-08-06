import type { AssetSource, Inspection, Job, Page, Recipe, RunEvent, Workspace } from "./types";

const base = "/api/v1";

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(payload.detail ?? `${response.status} ${response.statusText}`, response.status);
  }
  return response.status === 204 ? (undefined as T) : (response.json() as Promise<T>);
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  capabilities: () => request<Record<string, unknown>>("/capabilities"),
  listWorkspaces: () => request<Workspace[]>("/workspaces"),
  createWorkspace: (name: string) => request<Workspace>("/workspaces", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) }),
  listSources: (workspaceId: string) => request<AssetSource[]>(`/workspaces/${workspaceId}/sources`),
  listWorkspaceJobs: (workspaceId: string, limit = 200, offset = 0) =>
    request<Page<Job>>(`/workspaces/${workspaceId}/jobs?limit=${limit}&offset=${offset}`),
  uploadSource: (workspaceId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<AssetSource>(`/workspaces/${workspaceId}/sources`, { method: "POST", body });
  },
  inspectSource: (workspaceId: string, sourceId: string) => request<Inspection>(`/workspaces/${workspaceId}/sources/${sourceId}/inspection`),
  createJob: (workspaceId: string, sourceId: string, recipe: Recipe) => request<Job>(`/workspaces/${workspaceId}/sources/${sourceId}/jobs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ recipe }) }),
  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),
  listJobEvents: (jobId: string, after = 0, limit = 500) =>
    request<{ items: RunEvent[] }>(
      `/jobs/${jobId}/events.json?after=${after}&limit=${limit}&tail=${after === 0}`,
    ),
  cancelJob: (jobId: string) => request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  retryJob: (jobId: string) => request<Job>(`/jobs/${jobId}/retry`, { method: "POST" }),
  sourcePreviewUrl: (sourceId: string) => `${base}/assets/${sourceId}/preview`,
  eventUrl: (jobId: string) => `${base}/jobs/${jobId}/events`,
  artifactUrl: (jobId: string, name: string) => `${base}/jobs/${jobId}/artifacts/${encodeURIComponent(name)}`,
};

export function subscribeJobEvents(jobId: string, onEvent: (event: RunEvent) => void, onError: () => void): () => void {
  const stream = new EventSource(api.eventUrl(jobId));
  stream.onmessage = (message) => {
    try { onEvent(JSON.parse(message.data) as RunEvent); } catch { /* malformed event is ignored */ }
  };
  stream.onerror = onError;
  return () => stream.close();
}
