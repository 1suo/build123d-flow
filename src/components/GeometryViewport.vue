<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import Message from "primevue/message";
import ProgressSpinner from "primevue/progressspinner";
import type { MeshPreview } from "../types";

const props = defineProps<{ previews: Record<string, MeshPreview>; running?: boolean }>();
const host = ref<HTMLDivElement | null>(null);
let renderer: THREE.WebGLRenderer | undefined;
let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let controls: OrbitControls;
let resizeObserver: ResizeObserver | undefined;
let modelGroup: THREE.Group;
let animationFrame = 0;
let hasInitialFit = false;

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
      color: preview.color ?? 0xb68b54,
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
  scene.background = new THREE.Color("#17191c");
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

  const grid = new THREE.GridHelper(240, 24, 0x4a525b, 0x2c3239);
  grid.rotation.x = Math.PI / 2;
  scene.add(grid);
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

});

watch(() => props.previews, rebuild, { deep: true });

onBeforeUnmount(() => {
  cancelAnimationFrame(animationFrame);
  resizeObserver?.disconnect();
  controls?.dispose();
  renderer?.dispose();
  renderer?.domElement.remove();
});
</script>

<template>
  <div ref="host" class="geometry-viewport">
    <Message v-if="!Object.keys(previews).length && !running" severity="secondary" :closable="false" class="viewport-empty">
      No visible geometry
    </Message>
    <ProgressSpinner v-if="running" class="viewport-progress" style="width:22px;height:22px" stroke-width="4" />
  </div>
</template>

<style scoped>
.geometry-viewport { position: relative; width: 100%; height: auto; flex: 1 1 auto; min-height: 240px; overflow: hidden; }
.geometry-viewport :deep(canvas) { display: block; width: 100%; height: 100%; }
.viewport-empty { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); pointer-events: none; }
.viewport-progress { position: absolute; top: 14px; right: 14px; }
</style>