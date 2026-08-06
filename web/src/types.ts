export type JobState = "queued" | "running" | "accepted" | "candidate" | "failed" | "cancelled";

export interface Workspace {
  id: string;
  name: string;
  created_utc?: string;
  sources?: AssetSource[];
}

export interface AssetSource {
  id: string;
  name: string;
  kind?: string;
  hash?: string;
  preview_url?: string;
  inspection?: Inspection;
}

export interface Inspection {
  topology?: { vertices?: number; faces?: number; watertight?: boolean; components?: number };
  bounds?: { extents?: number[]; diagonal?: number };
  warnings?: string[];
  primitive_evidence?: PrimitiveEvidence[];
  scene?: { nodes?: number; geometries?: number; materials?: number };
}

export interface PrimitiveEvidence {
  kind: string;
  confidence: number;
  support_fraction?: number;
  summary?: string;
}

export interface RunEvent {
  sequence: number;
  created_utc: string;
  level: "debug" | "info" | "warning" | "error";
  type: string;
  stage?: string | null;
  message: string;
  data?: Record<string, unknown>;
}

export interface Artifact {
  name: string;
  kind?: string;
  url?: string;
  size?: number;
}

export interface Job {
  id: string;
  workspace_id?: string;
  source_id?: string;
  state: JobState;
  stage?: string;
  progress?: number;
  message?: string;
  cancel_requested?: boolean;
  created_utc?: string;
  updated_utc?: string;
  recipe?: Recipe;
  events?: RunEvent[];
  source_preview_url?: string;
  candidate_preview_url?: string;
  collision_preview_url?: string;
  artifacts?: Artifact[];
  inspection?: Inspection;
  metrics?: Record<string, unknown>;
  validation?: Record<string, unknown>;
}

export interface Recipe {
  preset: "close" | "balanced" | "distant" | "collision" | "custom";
  geometry: {
    enabled: boolean;
    strategy: "target" | "error";
    target_faces: number;
    max_error: number;
    preserve_boundaries: boolean;
    reconstruct_planar: boolean;
    reconstruct_primitives: boolean;
  };
  collision: {
    enabled: boolean;
    mode: "auto" | "box" | "sphere" | "cylinder" | "capsule" | "convex-hull" | "compound";
    fit: "cover" | "balanced" | "inside";
    body: "static" | "dynamic" | "area";
  };
  validation: {
    compare_geometry: boolean;
    compare_scene_inventory: boolean;
    compare_appearance: boolean;
  };
}

export const defaultRecipe: Recipe = {
  preset: "balanced",
  geometry: {
    enabled: true,
    strategy: "error",
    target_faces: 5000,
    max_error: 0.0025,
    preserve_boundaries: true,
    reconstruct_planar: false,
    reconstruct_primitives: false,
  },
  collision: { enabled: true, mode: "auto", fit: "balanced", body: "static" },
  validation: { compare_geometry: true, compare_scene_inventory: true, compare_appearance: false },
};
