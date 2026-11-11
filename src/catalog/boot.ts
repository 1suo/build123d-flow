import type { NodeDefinition } from "../types";

// Small synchronous catalog used to paint the workspace before Pyodide starts.
export const bootCatalog: NodeDefinition[] = [
  { id: "boot.Box", kind: "geometry", name: "Box", category: "Primitives", description: "Rectangular solid", inputs: [
    { name: "length", type: "Number", annotation: "Number", required: false, default: 1 },
    { name: "width", type: "Number", annotation: "Number", required: false, default: 1 },
    { name: "height", type: "Number", annotation: "Number", required: false, default: 1 },
  ], outputs: [{ name: "result", type: "Shape", annotation: "Shape" }] },
  { id: "boot.Cylinder", kind: "geometry", name: "Cylinder", category: "Primitives", description: "Cylindrical solid", inputs: [
    { name: "radius", type: "Number", annotation: "Number", required: false, default: 1 },
    { name: "height", type: "Number", annotation: "Number", required: false, default: 1 },
  ], outputs: [{ name: "result", type: "Shape", annotation: "Shape" }] },
  { id: "boot.Sphere", kind: "geometry", name: "Sphere", category: "Primitives", description: "Spherical solid", inputs: [
    { name: "radius", type: "Number", annotation: "Number", required: false, default: 1 },
  ], outputs: [{ name: "result", type: "Shape", annotation: "Shape" }] },
  { id: "boot.Rectangle", kind: "geometry", name: "Rectangle", category: "2D Profiles", description: "Rectangular planar profile", inputs: [
    { name: "width", type: "Number", annotation: "Number", required: false, default: 1 },
    { name: "height", type: "Number", annotation: "Number", required: false, default: 1 },
  ], outputs: [{ name: "result", type: "Shape", annotation: "Shape" }] },
  { id: "boot.Circle", kind: "geometry", name: "Circle", category: "2D Profiles", description: "Circular planar profile", inputs: [
    { name: "radius", type: "Number", annotation: "Number", required: false, default: 1 },
  ], outputs: [{ name: "result", type: "Shape", annotation: "Shape" }] },
  { id: "boot.Union", kind: "operation", name: "Union", category: "Boolean", description: "Combine two solids", inputs: [
    { name: "a", type: "Shape", annotation: "Shape", required: true },
    { name: "b", type: "Shape", annotation: "Shape", required: true },
  ], outputs: [{ name: "result", type: "Shape", annotation: "Shape" }] },
  { id: "boot.Difference", kind: "operation", name: "Difference", category: "Boolean", description: "Subtract one solid from another", inputs: [
    { name: "a", type: "Shape", annotation: "Shape", required: true },
    { name: "b", type: "Shape", annotation: "Shape", required: true },
  ], outputs: [{ name: "result", type: "Shape", annotation: "Shape" }] },
  { id: "boot.Intersection", kind: "operation", name: "Intersection", category: "Boolean", description: "Keep the common volume", inputs: [
    { name: "a", type: "Shape", annotation: "Shape", required: true },
    { name: "b", type: "Shape", annotation: "Shape", required: true },
  ], outputs: [{ name: "result", type: "Shape", annotation: "Shape" }] },
];
