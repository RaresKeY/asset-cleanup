import { defaultRecipe, type Job, type JobState, type Recipe, type RunEvent } from "./types";

export function recipeForPreset(preset: Recipe["preset"], current: Recipe = defaultRecipe): Recipe {
  if (preset === "custom") return { ...current, preset };
  const errors = { close: 0.001, balanced: 0.0025, distant: 0.01, collision: 0.01 };
  return {
    ...defaultRecipe,
    preset,
    geometry: {
      ...defaultRecipe.geometry,
      enabled: preset !== "collision",
      max_error: errors[preset],
    },
    collision: { ...defaultRecipe.collision },
    validation: { ...defaultRecipe.validation },
  };
}

export function isActiveJob(state: JobState | undefined): boolean {
  return state === "queued" || state === "running";
}

export function isTerminalJob(state: JobState | undefined): boolean {
  return state === "accepted" || state === "candidate" || state === "failed" || state === "cancelled";
}

export function jobProgressPercent(job: Pick<Job, "progress" | "state">): number {
  if (job.progress == null) return isActiveJob(job.state) ? 8 : 100;
  const percent = job.progress <= 1 ? job.progress * 100 : job.progress;
  return Math.round(Math.max(0, Math.min(100, percent)));
}

export function jobStage(job: Pick<Job, "stage" | "state">): string {
  if (job.stage) return job.stage.replaceAll("_", " ");
  if (job.state === "queued") return "queue";
  if (isTerminalJob(job.state)) return "complete";
  return "starting";
}

export function preferNewerJob(current: Job, incoming: Job): Job {
  if (current.id !== incoming.id) return incoming;
  const currentSequence = current.event_sequence ?? 0;
  const incomingSequence = incoming.event_sequence ?? 0;
  if (incomingSequence !== currentSequence) {
    return incomingSequence > currentSequence ? incoming : current;
  }
  if (isTerminalJob(current.state) !== isTerminalJob(incoming.state)) {
    return isTerminalJob(incoming.state) ? incoming : current;
  }
  if (current.updated_utc && incoming.updated_utc && current.updated_utc !== incoming.updated_utc) {
    return incoming.updated_utc > current.updated_utc ? incoming : current;
  }
  return jobProgressPercent(incoming) >= jobProgressPercent(current) ? incoming : current;
}

export function upsertJob(jobs: Job[], incoming: Job): Job[] {
  const existing = jobs.find((item) => item.id === incoming.id);
  const selected = existing ? preferNewerJob(existing, incoming) : incoming;
  const withoutIncoming = jobs.filter((item) => item.id !== incoming.id);
  return [selected, ...withoutIncoming].sort((left, right) =>
    (right.created_utc ?? "").localeCompare(left.created_utc ?? "") || right.id.localeCompare(left.id),
  );
}

export function mergeJobs(previous: Job[], incoming: Job[]): Job[] {
  return incoming.reduce((jobs, job) => upsertJob(jobs, job), previous);
}

export function mergeRunEvents(previous: RunEvent[], incoming: RunEvent[]): RunEvent[] {
  const events = new Map<number, RunEvent>();
  for (const event of [...previous, ...incoming]) events.set(event.sequence, event);
  return [...events.values()].sort((left, right) => left.sequence - right.sequence).slice(-500);
}

function compactLine(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function eventStage(event: RunEvent): string {
  const stage = event.stage ?? event.data?.stage;
  return typeof stage === "string" && stage ? stage : event.type;
}

export function formatRunEvent(event: RunEvent): string {
  const level = event.level.toUpperCase().padEnd(7);
  return `${event.created_utc} ${level} ${eventStage(event).padEnd(14)} ${compactLine(event.message)}`;
}

export function formatRunLog(job: Job, sourceName: string, events: RunEvent[]): string {
  const progress = jobProgressPercent(job);
  const summary = [
    `job=${job.id}`,
    `source=${JSON.stringify(sourceName)}`,
    `state=${job.state}`,
    `stage=${jobStage(job)}`,
    `progress=${progress}%`,
  ].join(" ");
  const details = [job.error_message, job.message]
    .filter((value, index, all): value is string => Boolean(value) && all.indexOf(value) === index)
    .map((value) => `status: ${compactLine(value)}`);
  const lines = events.map(formatRunEvent);
  return [summary, ...details, ...(lines.length ? lines : ["No events recorded."])].join("\n");
}

export function recipeWarnings(recipe: Recipe): string[] {
  const warnings: string[] = [];
  if (recipe.geometry.reconstruct_planar || recipe.geometry.reconstruct_primitives) {
    warnings.push("Regional reconstruction changes topology; existing UV and baked texture evidence will be marked stale.");
  }
  if (recipe.collision.enabled && recipe.collision.body === "dynamic" && recipe.collision.mode === "convex-hull") {
    warnings.push("A single convex hull may cover empty volume. Use compound or auto for a closer dynamic-body fit.");
  }
  if (recipe.validation.compare_appearance && recipe.geometry.enabled) {
    warnings.push("Appearance comparison needs a valid reference and may keep the result as a candidate if unavailable.");
  }
  return warnings;
}

export function formatCount(value: number | undefined): string {
  return value === undefined ? "—" : new Intl.NumberFormat().format(value);
}
