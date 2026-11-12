<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import ViewportStatus from "./ViewportStatus.vue";
import type { MeshPreview } from "../types";

const props = defineProps<{
  previews: Record<string, MeshPreview>;
  running?: boolean;
  runtimeStatus?: string;
  downloadStatus?: string;
  errors?: string[];
  logs?: Array<{ time: string; level: "info" | "success" | "warning" | "error"; message: string }>;
}>();
const host = ref<HTMLDivElement | null>(null);
let renderer: THREE.WebGLRenderer | undefined;
let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let controls: OrbitControls;
let resizeObserver: ResizeObserver | undefined;
let modelGroup: THREE.Group;
let gridHelper: THREE.GridHelper | undefined;
let themeObserver: MutationObserver | undefined;
let animationFrame = 0;
let hasInitialFit = false;

function cssColor(token: string, fallback: string): THREE.Color {
  const value = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  return new THREE.Color(value || fallback);
}

function applyTheme() {
  if (!scene) return;
  scene.background = cssColor("--tikal-background", "#013220");
  if (gridHelper) {
    const materials = Array.isArray(gridHelper.material) ? gridHelper.material : [gridHelper.material];
    (materials[0] as THREE.LineBasicMaterial | undefined)?.color.copy(cssColor("--tikal-selection", "#458b74"));
    (materials[1] as THREE.LineBasicMaterial | undefined)?.color.copy(cssColor("--tikal-border", "#2f4f4f"));
  }
  if (modelGroup) {
    for (const child of modelGroup.children) {
      if (!(child instanceof THREE.Mesh)) continue;
      const preview = props.previews[child.name];
      if (preview?.color === undefined) {
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        materials.forEach((material) => {
          if (material instanceof THREE.MeshStandardMaterial) material.color.copy(cssColor("--tikal-primary", "#7cfc00"));
        });
      }
    }
  }
  renderer?.render(scene, camera);
}

function rebuild() {
  if (!scene || !modelGroup) return;
  for (const child of modelGroup.children) {
    if (child instanceof THREE.Mesh) {
      child.geometry.dispose();
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      materials.forEach((material) => material.dispose());
    }
  }
  modelGroup.clear();
  for (const [id, preview] of Object.entries(props.previews)) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(preview.vertices, 3));
    geometry.setIndex(preview.triangles);
    geometry.computeVertexNormals();
    const material = new THREE.MeshStandardMaterial({
      color: preview.color ?? cssColor("--tikal-primary", "#7cfc00"),
      metalness: 0.12,
      roughness: 0.68,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = id;
    modelGroup.add(mesh);
  }
  const bounds = new THREE.Box3().setFromObject(modelGroup);
  // Fit once when the first model arrives. Subsequent evaluations never move the camera.
  if (!hasInitialFit && !bounds.isEmpty()) {
    const sphere = bounds.getBoundingSphere(new THREE.Sphere());
    controls.target.copy(sphere.center);
    const distance = Math.max(sphere.radius * 2.8, 45);
    camera.position.set(
      sphere.center.x + distance,
      sphere.center.y - distance,
      sphere.center.z + distance * 0.75,
    );
    camera.near = Math.max(distance / 1000, 0.01);
    camera.far = Math.max(distance * 100, 10000);
    camera.updateProjectionMatrix();
    controls.update();
    hasInitialFit = true;
  }
}

function resize() {
  if (!host.value || !renderer || !camera) return;
  const width = Math.max(host.value.clientWidth, 1);
  const height = Math.max(host.value.clientHeight, 1);
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}

onMounted(() => {
  if (!host.value) return;
  scene = new THREE.Scene();
  scene.background = cssColor("--tikal-background", "#013220");
  camera = new THREE.PerspectiveCamera(42, 1, 0.1, 10000);
  camera.up.set(0, 0, 1);
  camera.position.set(100, -100, 90);
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.value.appendChild(renderer.domElement);

  scene.add(new THREE.HemisphereLight(0xdce2e8, 0x30363d, 1.8));
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(80, -60, 120);
  scene.add(key);

  gridHelper = new THREE.GridHelper(
    240, 24,
    cssColor("--tikal-selection", "#458b74"),
    cssColor("--tikal-border", "#2f4f4f"),
  );
  gridHelper.rotation.x = Math.PI / 2;
  scene.add(gridHelper);
  scene.add(new THREE.AxesHelper(35));
  modelGroup = new THREE.Group();
  scene.add(modelGroup);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;

  const animate = () => {
    if (!renderer) return;
    controls.update();
    renderer.render(scene, camera);
    animationFrame = requestAnimationFrame(animate);
  };
  animationFrame = requestAnimationFrame(animate);
  resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host.value);
  resize();
  rebuild();
  themeObserver = new MutationObserver(applyTheme);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-findspot-theme"] });
  applyTheme();
});

watch(() => props.previews, rebuild, { deep: true });

onBeforeUnmount(() => {
  cancelAnimationFrame(animationFrame);
  resizeObserver?.disconnect();
  themeObserver?.disconnect();
  controls?.dispose();
  renderer?.dispose();
  renderer?.domElement.remove();
});
</script>

<template>
  <div ref="host" class="geometry-viewport">
    <ViewportStatus
      :geometry-count="Object.keys(previews).length"
      :running="Boolean(running)"
      :runtime-status="runtimeStatus || 'Ready'"
      :download-status="downloadStatus || ''"
      :errors="errors || []"
      :logs="logs || []"
    />
  </div>
</template>

<style scoped>
.geometry-viewport { position: relative; width: 100%; height: auto; flex: 1 1 auto; min-height: 240px; overflow: hidden; }
.geometry-viewport :deep(canvas) { display: block; width: 100%; height: 100%; }
</style>