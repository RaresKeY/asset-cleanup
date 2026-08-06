import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

export type CompareMode = "split" | "overlay";

interface SceneViewportProps {
  sourceUrl?: string;
  candidateUrl?: string;
  collisionUrl?: string;
  mode: CompareMode;
  wireframe: boolean;
  showCollision: boolean;
}

type AssetRole = "source" | "candidate" | "collision";

interface LoadedAsset {
  object: THREE.Object3D;
  role: AssetRole;
  basePosition: THREE.Vector3;
}

interface CameraFit {
  center: THREE.Vector3;
  distance: number;
  radius: number;
}

interface SavedCameraView {
  primaryUrl?: string;
  assetSignature: string;
  layoutSignature: CompareMode;
  position: THREE.Vector3;
  target: THREE.Vector3;
  near: number;
  far: number;
  zoom: number;
  userInteracted: boolean;
  hasFramed: boolean;
}

/** Pure framing calculation kept exported so camera behavior can be regression-tested without WebGL. */
// eslint-disable-next-line react-refresh/only-export-components
export function calculateCameraFit(
  box: THREE.Box3,
  verticalFovDegrees: number,
  aspect: number,
  padding = 1.18,
): CameraFit | undefined {
  if (box.isEmpty()) return undefined;

  const sphere = box.getBoundingSphere(new THREE.Sphere());
  if (!Number.isFinite(sphere.radius)) return undefined;
  const radius = Math.max(sphere.radius, 0.001);
  const verticalFov = THREE.MathUtils.degToRad(verticalFovDegrees);
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * Math.max(aspect, 0.01));
  const limitingFov = Math.max(Math.min(verticalFov, horizontalFov), THREE.MathUtils.degToRad(1));
  const distance = (radius / Math.sin(limitingFov / 2)) * padding;
  return { center: sphere.center.clone(), distance, radius };
}

/** Decide whether a renderer rebuild should retain the current camera exactly. */
// eslint-disable-next-line react-refresh/only-export-components
export function shouldPreserveCameraView(
  previousPrimaryUrl: string | undefined,
  nextPrimaryUrl: string | undefined,
  previousAssetSignature: string,
  nextAssetSignature: string,
  previousLayoutSignature: CompareMode,
  nextLayoutSignature: CompareMode,
  userInteracted: boolean,
  hasFramed: boolean,
): boolean {
  if (
    !hasFramed
    || previousPrimaryUrl !== nextPrimaryUrl
    || previousLayoutSignature !== nextLayoutSignature
  ) return false;
  if (previousAssetSignature === nextAssetSignature) return true;
  // Overlay assets retain source coordinates. Split assets are translated from
  // their source coordinates whenever either side changes, so the old camera
  // cannot safely be retained even after user interaction.
  return userInteracted && nextLayoutSignature === "overlay";
}

/** Auto-framing waits for stable source/candidate world-space layout. */
// eslint-disable-next-line react-refresh/only-export-components
export function shouldFrameSettledLayout(
  layoutRequestsPending: number,
  preserveCamera: boolean,
  userInteracted: boolean,
  hasLayoutAsset: boolean,
): boolean {
  return layoutRequestsPending === 0 && !preserveCamera && !userInteracted && hasLayoutAsset;
}

/** Keep graphics-startup failures inside the viewport instead of crashing React. */
// eslint-disable-next-line react-refresh/only-export-components
export function rendererStartupErrorMessage(error: unknown): string {
  const detail = error instanceof Error ? error.message.trim() : "";
  return detail ? `The 3D renderer could not start: ${detail}` : "The 3D renderer could not start on this device.";
}

/** Clone preview materials without changing Three's scalar-versus-grouped material contract. */
// eslint-disable-next-line react-refresh/only-export-components
export function cloneMaterialMode(
  material: THREE.Material | THREE.Material[],
  wireframe: boolean,
  opacity: number,
): THREE.Material | THREE.Material[] {
  const materialWasArray = Array.isArray(material);
  const sourceMaterials = materialWasArray ? material : [material];
  const clonedMaterials = sourceMaterials.map((sourceMaterial) => {
    const clone = sourceMaterial.clone();
    if ("wireframe" in clone) clone.wireframe = wireframe;
    clone.transparent = sourceMaterial.transparent || opacity < 1;
    clone.opacity = sourceMaterial.opacity * opacity;
    clone.depthWrite = sourceMaterial.depthWrite && opacity >= 1;
    return clone;
  });
  return materialWasArray ? clonedMaterials : clonedMaterials[0];
}

function applyMaterialMode(root: THREE.Object3D, wireframe: boolean, opacity: number): void {
  root.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    node.material = cloneMaterialMode(node.material, wireframe, opacity);
  });
}

function tintCollision(root: THREE.Object3D): void {
  root.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    materials.forEach((material) => material.color.set("#f3b44b"));
  });
}

function disposeObject(root: THREE.Object3D): void {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();

  root.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    geometries.add(node.geometry);
    const nodeMaterials = Array.isArray(node.material) ? node.material : [node.material];
    nodeMaterials.forEach((material) => {
      materials.add(material);
      Object.values(material).forEach((value: unknown) => {
        if (value instanceof THREE.Texture) textures.add(value);
      });
    });
  });

  textures.forEach((texture) => texture.dispose());
  materials.forEach((material) => material.dispose());
  geometries.forEach((geometry) => geometry.dispose());
}

function boxFor(object: THREE.Object3D): THREE.Box3 {
  object.updateWorldMatrix(true, true);
  return new THREE.Box3().setFromObject(object, true);
}

function layoutAssets(assets: LoadedAsset[], mode: CompareMode): void {
  assets.forEach(({ object, basePosition }) => object.position.copy(basePosition));
  if (mode !== "split") return;

  const source = assets.find((asset) => asset.role === "source");
  const candidate = assets.find((asset) => asset.role === "candidate");
  if (!source || !candidate) return;

  const sourceBox = boxFor(source.object);
  const candidateBox = boxFor(candidate.object);
  if (sourceBox.isEmpty() || candidateBox.isEmpty()) return;

  const sourceSize = sourceBox.getSize(new THREE.Vector3());
  const candidateSize = candidateBox.getSize(new THREE.Vector3());
  const largestExtent = Math.max(sourceSize.length(), candidateSize.length(), 0.1);
  const gap = Math.max(largestExtent * 0.08, 0.025);
  const sourceOffset = -gap / 2 - sourceBox.max.x;
  const candidateOffset = gap / 2 - candidateBox.min.x;

  source.object.position.x += sourceOffset;
  candidate.object.position.x += candidateOffset;
  assets
    .filter((asset) => asset.role === "collision")
    .forEach((asset) => { asset.object.position.x += candidateOffset; });
}

function contentBox(assets: LoadedAsset[]): THREE.Box3 {
  const box = new THREE.Box3();
  assets.forEach(({ object }) => box.union(boxFor(object)));
  return box;
}

function frameContent(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
  grid: THREE.GridHelper,
  assets: LoadedAsset[],
): void {
  const box = contentBox(assets);
  const fit = calculateCameraFit(box, camera.fov, camera.aspect);
  if (!fit) return;

  const viewDirection = new THREE.Vector3(1, 0.72, 1).normalize();
  camera.position.copy(fit.center).addScaledVector(viewDirection, fit.distance);
  camera.near = Math.max(fit.distance - fit.radius * 2.2, fit.radius / 10_000, 0.0001);
  camera.far = Math.max(fit.distance + fit.radius * 12, 10);
  camera.updateProjectionMatrix();

  controls.target.copy(fit.center);
  controls.minDistance = Math.max(fit.radius * 0.03, 0.0001);
  controls.maxDistance = Math.max(fit.radius * 40, 20);
  controls.update();

  const size = box.getSize(new THREE.Vector3());
  const gridSize = Math.max(size.x, size.z, fit.radius * 1.4, 0.1);
  grid.scale.setScalar(gridSize / 10);
  grid.position.set(fit.center.x, box.min.y, fit.center.z);
}

export function SceneViewport({ sourceUrl, candidateUrl, collisionUrl, mode, wireframe, showCollision }: SceneViewportProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const savedCameraRef = useRef<SavedCameraView | undefined>(undefined);
  const [loadState, setLoadState] = useState<"empty" | "loading" | "ready" | "error">("empty");
  const [loadError, setLoadError] = useState<string>();
  const requestedCollisionUrl = showCollision ? collisionUrl : undefined;

  useEffect(() => {
    const host = rootRef.current;
    if (!host) return undefined;

    const primaryUrl = sourceUrl ?? candidateUrl;
    // Collision overlays are expected to share candidate coordinates, so toggling one
    // is a presentation change and must not disturb the current camera.
    const assetSignature = [sourceUrl ?? "", candidateUrl ?? ""].join("\n");
    const previousView = savedCameraRef.current;
    const preserveCamera = previousView ? shouldPreserveCameraView(
      previousView.primaryUrl,
      primaryUrl,
      previousView.assetSignature,
      assetSignature,
      previousView.layoutSignature,
      mode,
      previousView.userInteracted,
      previousView.hasFramed,
    ) : false;
    const requests: Array<[string, AssetRole]> = [];
    if (sourceUrl) requests.push([sourceUrl, "source"]);
    if (candidateUrl) requests.push([candidateUrl, "candidate"]);
    if (requestedCollisionUrl) requests.push([requestedCollisionUrl, "collision"]);
    setLoadState(requests.length ? "loading" : "empty");
    setLoadError(undefined);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#080a0d");
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 10_000);
    if (preserveCamera && previousView) {
      camera.position.copy(previousView.position);
      camera.near = previousView.near;
      camera.far = previousView.far;
      camera.zoom = previousView.zoom;
      camera.updateProjectionMatrix();
    } else {
      camera.position.set(3, 2, 3);
    }
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
    } catch (error) {
      host.replaceChildren();
      setLoadError(rendererStartupErrorMessage(error));
      setLoadState("error");
      return () => { host.replaceChildren(); };
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute("aria-label", "Interactive 3D asset comparison. Drag to orbit, scroll to zoom.");
    host.replaceChildren(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    controls.listenToKeyEvents(renderer.domElement);
    if (preserveCamera && previousView) {
      controls.target.copy(previousView.target);
      controls.update();
    }

    const grid = new THREE.GridHelper(10, 20, 0x303842, 0x171c22);
    const hemisphere = new THREE.HemisphereLight(0xe7efff, 0x171b22, 2.2);
    const key = new THREE.DirectionalLight(0xffffff, 2.6);
    key.position.set(4, 6, 5);
    const fill = new THREE.DirectionalLight(0x94bfff, 0.85);
    fill.position.set(-4, 2, -3);
    scene.add(grid, hemisphere, key, fill);

    const loader = new GLTFLoader();
    const loaded: LoadedAsset[] = [];
    let disposed = false;
    let pending = requests.length;
    let layoutPending = requests.filter(([, role]) => role !== "collision").length;
    let failed = false;
    let userMovedCamera = preserveCamera ? previousView?.userInteracted ?? false : false;
    let hasFramed = preserveCamera;

    // Until both source and candidate settle, split layout is not stable. Keeping
    // controls disabled prevents interaction with a temporary placement that may
    // move as soon as the slower request finishes.
    controls.enabled = preserveCamera || layoutPending === 0;
    controls.addEventListener("start", () => { userMovedCamera = true; });

    const canFrameLoadedLayout = () => shouldFrameSettledLayout(
      layoutPending,
      preserveCamera,
      userMovedCamera,
      loaded.some(({ role }) => role !== "collision"),
    );

    const resize = () => {
      if (disposed) return;
      const { width, height } = host.getBoundingClientRect();
      const safeWidth = Math.max(Math.round(width), 1);
      const safeHeight = Math.max(Math.round(height), 1);
      renderer.setSize(safeWidth, safeHeight, false);
      camera.aspect = Math.max(safeWidth / safeHeight, 0.01);
      camera.updateProjectionMatrix();
      if (canFrameLoadedLayout()) {
        frameContent(camera, controls, grid, loaded);
        hasFramed = true;
      }
    };

    const settleRequest = (role: AssetRole) => {
      pending -= 1;
      if (role !== "collision") {
        layoutPending -= 1;
        if (layoutPending === 0) controls.enabled = true;
      }
      if (canFrameLoadedLayout()) {
        frameContent(camera, controls, grid, loaded);
        hasFramed = true;
      }
      if (!disposed && pending === 0 && loaded.length && !failed) setLoadState("ready");
    };

    requests.forEach(([url, role]) => {
      loader.load(
        url,
        (gltf) => {
          if (disposed) {
            disposeObject(gltf.scene);
            return;
          }

          const object = gltf.scene;
          if (role === "candidate") applyMaterialMode(object, wireframe, mode === "overlay" ? 0.58 : 1);
          if (role === "source") applyMaterialMode(object, wireframe, mode === "overlay" ? 0.42 : 1);
          if (role === "collision") {
            applyMaterialMode(object, true, 0.82);
            tintCollision(object);
          }
          scene.add(object);
          loaded.push({ object, role, basePosition: object.position.clone() });
          layoutAssets(loaded, mode);
          settleRequest(role);
        },
        undefined,
        (error) => {
          if (disposed) return;
          failed = true;
          const reason = error instanceof Error && error.message ? error.message : "The preview could not be decoded.";
          setLoadError(reason);
          setLoadState("error");
          settleRequest(role);
        },
      );
    });

    const onContextLost = (event: Event) => {
      event.preventDefault();
      if (!disposed) {
        setLoadError("The 3D renderer lost its graphics context. Reload the page to restore it.");
        setLoadState("error");
      }
    };
    renderer.domElement.addEventListener("webglcontextlost", onContextLost);

    const resizeObserver = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(resize);
    resizeObserver?.observe(host);
    window.addEventListener("resize", resize);
    resize();

    renderer.setAnimationLoop(() => {
      controls.update();
      renderer.render(scene, camera);
    });

    return () => {
      disposed = true;
      savedCameraRef.current = {
        primaryUrl,
        assetSignature,
        layoutSignature: mode,
        position: camera.position.clone(),
        target: controls.target.clone(),
        near: camera.near,
        far: camera.far,
        zoom: camera.zoom,
        userInteracted: userMovedCamera,
        hasFramed,
      };
      resizeObserver?.disconnect();
      window.removeEventListener("resize", resize);
      renderer.domElement.removeEventListener("webglcontextlost", onContextLost);
      renderer.setAnimationLoop(null);
      controls.stopListenToKeyEvents();
      controls.dispose();
      loaded.forEach(({ object }) => disposeObject(object));
      grid.geometry.dispose();
      const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
      gridMaterials.forEach((material) => material.dispose());
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    };
  }, [sourceUrl, candidateUrl, requestedCollisionUrl, mode, wireframe]);

  const hasModel = Boolean(sourceUrl || candidateUrl);
  return (
    <div className="scene-frame" aria-busy={loadState === "loading"}>
      <div className="scene-canvas" ref={rootRef} role="region" aria-label="3D source and candidate comparison viewport" />
      {!hasModel && <div className="scene-empty"><span aria-hidden="true">◇</span><strong>Select or drop an asset</strong><p>GLB, embedded glTF, OBJ, PLY, and STL sources appear here after inspection.</p></div>}
      {hasModel && loadState === "loading" && <div className="scene-notice" role="status"><span className="scene-spinner" aria-hidden="true" /> Loading preview…</div>}
      {loadState === "error" && <div className="scene-notice error" role="alert"><strong>Preview unavailable</strong><span>{loadError}</span></div>}
      {hasModel && <div className="scene-key"><span><i className="source-dot" /> source</span>{candidateUrl && <span><i className="candidate-dot" /> candidate</span>}{showCollision && collisionUrl && <span><i className="collision-dot" /> collision</span>}</div>}
    </div>
  );
}
