import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  calculateCameraFit,
  cloneMaterialMode,
  rendererStartupErrorMessage,
  shouldFrameSettledLayout,
  shouldPreserveCameraView,
} from "./SceneViewport";

describe("SceneViewport camera framing", () => {
  it("centers the camera fit on translated model bounds", () => {
    const box = new THREE.Box3(new THREE.Vector3(4, -2, 8), new THREE.Vector3(10, 6, 12));

    const fit = calculateCameraFit(box, 45, 16 / 9);

    expect(fit).toBeDefined();
    expect(fit?.center.toArray()).toEqual([7, 2, 10]);
    expect(fit?.distance).toBeGreaterThan(fit?.radius ?? 0);
  });

  it("moves farther away for a narrow portrait viewport", () => {
    const box = new THREE.Box3(new THREE.Vector3(-5, -1, -1), new THREE.Vector3(5, 1, 1));

    const landscape = calculateCameraFit(box, 45, 16 / 9);
    const portrait = calculateCameraFit(box, 45, 9 / 16);

    expect(portrait?.distance).toBeGreaterThan(landscape?.distance ?? Number.POSITIVE_INFINITY);
  });

  it("does not invent a camera fit for empty content", () => {
    expect(calculateCameraFit(new THREE.Box3(), 45, 1)).toBeUndefined();
  });

  it("preserves a framed camera across presentation-only renderer rebuilds", () => {
    expect(shouldPreserveCameraView("source.glb", "source.glb", "assets", "assets", "split", "split", false, true)).toBe(true);
  });

  it("preserves an interacted camera as an overlay candidate arrives", () => {
    expect(shouldPreserveCameraView("source.glb", "source.glb", "source", "source+candidate", "overlay", "overlay", true, true)).toBe(true);
  });

  it("allows automatic framing for new content when the user has not moved the camera", () => {
    expect(shouldPreserveCameraView("source.glb", "source.glb", "source", "source+candidate", "split", "split", false, true)).toBe(false);
    expect(shouldPreserveCameraView("old.glb", "new.glb", "assets", "assets", "split", "split", true, true)).toBe(false);
  });

  it("refits when split and overlay layouts move assets in world space", () => {
    expect(shouldPreserveCameraView("source.glb", "source.glb", "assets", "assets", "split", "overlay", true, true)).toBe(false);
  });

  it("refits when a split candidate translates an already viewed source", () => {
    expect(shouldPreserveCameraView("source.glb", "source.glb", "source-at-x-100", "source+candidate", "split", "split", true, true)).toBe(false);
  });

  it("defers framing until every layout-affecting request settles", () => {
    expect(shouldFrameSettledLayout(1, false, false, true)).toBe(false);
    expect(shouldFrameSettledLayout(0, false, false, true)).toBe(true);
  });

  it("frames available content after its peer fails without overriding preserved views", () => {
    expect(shouldFrameSettledLayout(0, false, false, true)).toBe(true);
    expect(shouldFrameSettledLayout(0, true, false, true)).toBe(false);
    expect(shouldFrameSettledLayout(0, false, false, false)).toBe(false);
  });
});

describe("SceneViewport renderer startup", () => {
  it("turns WebGL construction failures into viewport-safe messages", () => {
    expect(rendererStartupErrorMessage(new Error("WebGL is disabled"))).toBe(
      "The 3D renderer could not start: WebGL is disabled",
    );
    expect(rendererStartupErrorMessage("context unavailable")).toBe(
      "The 3D renderer could not start on this device.",
    );
  });
});

describe("SceneViewport material preview mode", () => {
  it("keeps ordinary GLB mesh materials scalar so ungrouped geometry renders", () => {
    const source = new THREE.MeshStandardMaterial({ opacity: 0.8 });

    const result = cloneMaterialMode(source, true, 0.5);

    expect(Array.isArray(result)).toBe(false);
    expect(result).not.toBe(source);
    expect((result as THREE.MeshStandardMaterial).wireframe).toBe(true);
    expect((result as THREE.Material).opacity).toBeCloseTo(0.4);
    (result as THREE.Material).dispose();
    source.dispose();
  });

  it("keeps grouped multi-material meshes as arrays", () => {
    const sources = [new THREE.MeshBasicMaterial(), new THREE.MeshBasicMaterial()];
    const result = cloneMaterialMode(sources, false, 1);

    expect(Array.isArray(result)).toBe(true);
    expect(result).toHaveLength(2);
    (result as THREE.Material[]).forEach((material) => material.dispose());
    sources.forEach((material) => material.dispose());
  });
});
