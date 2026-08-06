import { describe, expect, it } from "vitest";
import { defaultRecipe, type Job, type RunEvent } from "./types";
import {
  formatRunLog,
  isActiveJob,
  isTerminalJob,
  jobProgressPercent,
  mergeJobs,
  mergeRunEvents,
  preferNewerJob,
  recipeForPreset,
  recipeWarnings,
  upsertJob,
} from "./state";

describe("job state helpers", () => {
  it("distinguishes active and terminal states", () => {
    expect(isActiveJob("running")).toBe(true);
    expect(isTerminalJob("accepted")).toBe(true);
    expect(isTerminalJob("running")).toBe(false);
  });

  it("discloses topology-changing recipes", () => {
    expect(recipeWarnings({ ...defaultRecipe, geometry: { ...defaultRecipe.geometry, reconstruct_planar: true } })).toHaveLength(1);
  });

  it("expands preset selection instead of retaining stale nested settings", () => {
    const close = recipeForPreset("close", {
      ...defaultRecipe,
      geometry: { ...defaultRecipe.geometry, max_error: 0.5 },
    });
    const collision = recipeForPreset("collision", close);

    expect(close.geometry.max_error).toBe(0.001);
    expect(collision.geometry.enabled).toBe(false);
    expect(collision.preset).toBe("collision");
  });

  it("keeps native validation explicit through defaults and preset expansion", () => {
    expect(defaultRecipe.validation.gltf_validator).toBe(true);

    const disabled = {
      ...defaultRecipe,
      preset: "custom" as const,
      validation: { ...defaultRecipe.validation, gltf_validator: false },
    };

    expect(recipeForPreset("custom", disabled).validation.gltf_validator).toBe(false);
    expect(recipeForPreset("balanced", disabled).validation.gltf_validator).toBe(true);
  });

  it("keeps restored jobs newest-first while replacing refreshed records", () => {
    const older = { id: "older", state: "queued", created_utc: "2026-01-01T00:00:00Z" } as Job;
    const newer = { id: "newer", state: "running", created_utc: "2026-01-02T00:00:00Z" } as Job;
    const refreshed = { ...older, state: "accepted" as const, progress: 1 };

    expect(upsertJob([older, newer], refreshed)).toEqual([newer, refreshed]);
    expect(jobProgressPercent(newer)).toBe(8);
    expect(jobProgressPercent(refreshed)).toBe(100);
  });

  it("never lets an older overlapping refresh regress a live or terminal job", () => {
    const running = {
      id: "same",
      state: "running",
      event_sequence: 9,
      progress: 0.85,
      updated_utc: "2026-01-02T00:00:09Z",
    } as Job;
    const stale = {
      ...running,
      event_sequence: 8,
      progress: 0.5,
      updated_utc: "2026-01-02T00:00:08Z",
    };
    const complete = {
      ...running,
      state: "accepted" as const,
      event_sequence: 10,
      progress: 1,
      updated_utc: "2026-01-02T00:00:10Z",
    };

    expect(preferNewerJob(running, stale)).toBe(running);
    expect(preferNewerJob(complete, running)).toBe(complete);
    expect(mergeJobs([running], [stale, complete])).toEqual([complete]);
  });

  it("deduplicates resumed event streams and emits a copyable job log", () => {
    const started: RunEvent = {
      sequence: 1,
      created_utc: "2026-01-02T03:04:05Z",
      level: "info",
      type: "stage.started",
      stage: "geometry",
      message: "Building\nvisual candidate",
    };
    const finished: RunEvent = { ...started, sequence: 2, type: "stage.succeeded", message: "Done" };
    const merged = mergeRunEvents([started], [started, finished]);
    const job = {
      id: "abc123",
      source_id: "source-1",
      state: "running",
      stage: "geometry",
      progress: 0.5,
      message: "Building visual candidate",
    } as Job;
    const log = formatRunLog(job, "box.glb", merged);

    expect(merged.map((event) => event.sequence)).toEqual([1, 2]);
    expect(log).toContain('job=abc123 source="box.glb" state=running stage=geometry progress=50%');
    expect(log).toContain("Building visual candidate");
    expect(log).not.toContain("Building\nvisual");
  });
});
