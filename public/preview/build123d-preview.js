/* Contract-1 Perigram preview surface for opaque build123d values.
 * It consumes only JSON mesh payloads attached beside native value handles.
 */

export default function createBuild123dPreview({ vue, locale }) {
  const { h, computed, defineComponent } = vue;
  const emptyLabel = "No CAD geometry in this preview";

  return defineComponent({
    name: "Build123dCadPreview",
    props: {
      record: { type: Object, default: null },
      payload: { type: Object, default: null }
    },
    setup(props) {
      const meshes = computed(() => collectMeshes(props.payload));
      const drawing = computed(() => projectMeshes(meshes.value));

      return () => {
        const scene = drawing.value;
        if (!scene.polygons.length) {
          return h("div", {
            class: "flex min-h-48 items-center justify-center rounded-lg border border-dashed p-6 text-sm opacity-70",
            role: "status"
          }, emptyLabel);
        }
        return h("div", {
          class: "w-full overflow-hidden rounded-lg border",
          "data-build123d-preview": "mesh"
        }, [
          h("svg", {
            viewBox: "0 0 720 480",
            role: "img",
            "aria-label": "build123d CAD geometry preview",
            class: "block h-auto w-full",
            preserveAspectRatio: "xMidYMid meet"
          }, [
            h("rect", { x: 0, y: 0, width: 720, height: 480, fill: "transparent" }),
            ...scene.polygons.map((polygon, index) => h("polygon", {
              key: "face-" + index,
              points: polygon.points,
              fill: polygon.color,
              "fill-opacity": polygon.opacity,
              stroke: "#334155",
              "stroke-width": 0.65,
              "stroke-linejoin": "round",
              "vector-effect": "non-scaling-stroke"
            })),
            ...scene.edges.map((edge, index) => h("line", {
              key: "edge-" + index,
              x1: edge[0], y1: edge[1], x2: edge[2], y2: edge[3],
              stroke: "#1e293b",
              "stroke-width": 1.1,
              "vector-effect": "non-scaling-stroke"
            }))
          ])
        ]);
      };
    }
  });
}

function collectMeshes(root) {
  const found = new Map();
  const seen = new Set();
  function visit(value, depth = 0) {
    if (depth > 32 || value == null || typeof value !== "object" || seen.has(value)) return;
    seen.add(value);
    if (value.__perigram_runtime__ === "value-ref") {
      const preview = value.preview;
      if (preview?.renderer === "cad-mesh" && isMesh(preview.payload)) {
        found.set(value.id || "mesh-" + found.size, preview.payload);
      }
      return;
    }
    if (Array.isArray(value)) {
      for (const item of value) visit(item, depth + 1);
      return;
    }
    for (const item of Object.values(value)) visit(item, depth + 1);
  }
  visit(root);
  return [...found.values()];
}

function isMesh(value) {
  return Array.isArray(value?.vertices) &&
    Array.isArray(value?.triangles) &&
    value.vertices.length >= 9 &&
    value.triangles.length >= 3;
}

function projectMeshes(meshes) {
  const vertices = [];
  const faces = [];
  const rawEdges = [];

  for (const mesh of meshes) {
    const points = [];
    for (let index = 0; index + 2 < mesh.vertices.length; index += 3) {
      const x = Number(mesh.vertices[index]) || 0;
      const y = Number(mesh.vertices[index + 1]) || 0;
      const z = Number(mesh.vertices[index + 2]) || 0;
      points.push([0.8660254 * (x - y), 0.5 * (x + y) - z, x + y + z]);
    }
    const offset = vertices.length;
    vertices.push(...points);
    for (let index = 0; index + 2 < mesh.triangles.length; index += 3) {
      const a = offset + Number(mesh.triangles[index]);
      const b = offset + Number(mesh.triangles[index + 1]);
      const c = offset + Number(mesh.triangles[index + 2]);
      if ([a, b, c].every(value => Number.isInteger(value) && value >= offset && value < offset + points.length)) {
        faces.push({
          indices: [a, b, c],
          depth: (vertices[a][2] + vertices[b][2] + vertices[c][2]) / 3,
          color: mesh.faceColor || "#a6b6c8",
          opacity: Number.isFinite(Number(mesh.opacity)) ? Number(mesh.opacity) : 1
        });
      }
    }
    for (let index = 0; index + 5 < (mesh.edges || []).length; index += 6) {
      const edge = mesh.edges.slice(index, index + 6).map(value => Number(value) || 0);
      rawEdges.push(edge.map((value, coordinate) => coordinate % 3 === 0
        ? 0.8660254 * (value - edge[coordinate + 1])
        : coordinate % 3 === 1
          ? 0.5 * (edge[coordinate - 1] + value) - edge[coordinate + 1]
          : value
      ));
    }
  }

  if (!vertices.length) return { polygons: [], edges: [] };
  const xs = vertices.map(point => point[0]);
  const ys = vertices.map(point => point[1]);
  const minX = xs.reduce((value, item) => Math.min(value, item), Infinity);
  const maxX = xs.reduce((value, item) => Math.max(value, item), -Infinity);
  const minY = ys.reduce((value, item) => Math.min(value, item), Infinity);
  const maxY = ys.reduce((value, item) => Math.max(value, item), -Infinity);
  const rangeX = Math.max(1e-6, maxX - minX);
  const rangeY = Math.max(1e-6, maxY - minY);
  const scale = Math.min(640 / rangeX, 400 / rangeY);
  const transform = point => [
    360 + (point[0] - (minX + maxX) / 2) * scale,
    240 - (point[1] - (minY + maxY) / 2) * scale
  ];

  faces.sort((left, right) => left.depth - right.depth);
  const polygons = faces.map(face => {
    const projected = face.indices.map(index => transform(vertices[index]));
    return {
      points: projected.map(point => point.map(value => value.toFixed(2)).join(",")).join(" "),
      color: face.color,
      opacity: face.opacity
    };
  });
  const edges = rawEdges.map(edge => {
    const first = transform([edge[0], edge[1]]);
    const second = transform([edge[3], edge[4]]);
    return [first[0], first[1], second[0], second[1]];
  });
  return { polygons, edges };
}
