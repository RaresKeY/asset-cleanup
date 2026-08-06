import { describe, expect, it } from "vitest";
import { defaultRecipe } from "./types";
import { isActiveJob, isTerminalJob, recipeForPreset, recipeWarnings } from "./state";

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
});
