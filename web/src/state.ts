import { defaultRecipe, type JobState, type Recipe } from "./types";

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
