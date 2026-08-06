import { useEffect, useRef } from "react";
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

function applyMaterialMode(root: THREE.Object3D, wireframe: boolean, opacity: number): void {
  root.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    const sourceMaterials = Array.isArray(node.material) ? node.material : [node.material];
    node.material = sourceMaterials.map((material) => {
      const clone = material.clone();
      clone.wireframe = wireframe;
      clone.transparent = opacity < 1;
      clone.opacity = opacity;
      return clone;
    });
  });
}

function frame(camera: THREE.PerspectiveCamera, controls: OrbitControls, objects: THREE.Object3D[]): void {
  const box = new THREE.Box3();
  objects.forEach((object) => box.expandByObject(object));
  if (box.isEmpty()) return;
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const distance = Math.max(size.length() * 0.8, 1);
  camera.position.copy(center).add(new THREE.Vector3(distance, distance * 0.65, distance));
  camera.near = Math.max(distance / 100, 0.01);
  camera.far = distance * 100;
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

export function SceneViewport({ sourceUrl, candidateUrl, collisionUrl, mode, wireframe, showCollision }: SceneViewportProps) {
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = rootRef.current;
    if (!host) return undefined;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#101722");
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 10000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const grid = new THREE.GridHelper(10, 20, 0x334155, 0x1e293b);
    scene.add(grid, new THREE.HemisphereLight(0xdbeafe, 0x1f2937, 2.4));
    const key = new THREE.DirectionalLight(0xffffff, 2.5);
    key.position.set(4, 6, 5);
    scene.add(key);
    const loader = new GLTFLoader();
    let disposed = false;
    const loaded: THREE.Object3D[] = [];
    const load = (url: string | undefined, role: "source" | "candidate" | "collision") => {
      if (!url) return;
      loader.load(url, (gltf) => {
        if (disposed) return;
        const object = gltf.scene;
        if (role === "candidate" && mode === "split") object.position.x = 1.5;
        if (role === "source" && mode === "split") object.position.x = -1.5;
        if (role === "candidate") applyMaterialMode(object, wireframe, mode === "overlay" ? 0.58 : 1);
        if (role === "source") applyMaterialMode(object, wireframe, mode === "overlay" ? 0.4 : 1);
        if (role === "collision") {
          applyMaterialMode(object, true, 0.85);
          object.traverse((node) => {
            if (node instanceof THREE.Mesh) {
              const materials = Array.isArray(node.material) ? node.material : [node.material];
              materials.forEach((material) => material.color.set("#f59e0b"));
            }
          });
        }
        scene.add(object);
        loaded.push(object);
        frame(camera, controls, loaded);
      });
    };
    load(sourceUrl, "source");
    load(candidateUrl, "candidate");
    if (showCollision) load(collisionUrl, "collision");
    const resize = () => {
      const { width, height } = host.getBoundingClientRect();
      renderer.setSize(Math.max(width, 1), Math.max(height, 1), false);
      camera.aspect = Math.max(width / Math.max(height, 1), 0.1);
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();
    renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
    return () => {
      disposed = true;
      observer.disconnect();
      renderer.setAnimationLoop(null);
      loaded.forEach((object) => object.traverse((node) => {
        if (!(node instanceof THREE.Mesh)) return;
        node.geometry.dispose();
        const materials = Array.isArray(node.material) ? node.material : [node.material];
        materials.forEach((material) => material.dispose());
      }));
      controls.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [sourceUrl, candidateUrl, collisionUrl, mode, wireframe, showCollision]);

  const hasModel = Boolean(sourceUrl || candidateUrl);
  return (
    <div className="scene-frame">
      <div className="scene-canvas" ref={rootRef} aria-label="Interactive 3D source and candidate comparison viewport" />
      {!hasModel && <div className="scene-empty"><span>✦</span><strong>Choose a source to begin</strong><p>Upload a GLB, glTF, OBJ, PLY, or STL. The comparison viewport will stay attached to the run.</p></div>}
      {hasModel && <div className="scene-key"><span><i className="source-dot" /> source</span>{candidateUrl && <span><i className="candidate-dot" /> candidate</span>}{showCollision && collisionUrl && <span><i className="collision-dot" /> collision</span>}</div>}
    </div>
  );
}
