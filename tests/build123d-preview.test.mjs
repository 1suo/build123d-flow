import test from "node:test";
import assert from "node:assert/strict";
import createBuild123dPreview from "../public/preview/build123d-preview.js";

test("CAD preview surface renders meshes attached to opaque node outputs", () => {
  const vue = {
    h: (tag, props, children) => ({ tag, props, children }),
    computed: getter => ({ get value() { return getter(); } }),
    defineComponent: definition => definition
  };
  const component = createBuild123dPreview({ vue, locale: {} });
  const render = component.setup({
    record: null,
    payload: {
      nodes: {
        box: {
          outputs: {
            result: {
              __perigram_runtime__: "value-ref",
              id: "opaque-box",
              preview: {
                renderer: "cad-mesh",
                payload: {
                  vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
                  triangles: [0, 1, 2],
                  edges: [0, 0, 0, 1, 0, 0]
                }
              }
            }
          }
        }
      }
    }
  });
  const root = render();
  assert.equal(root.tag, "div");
  const svg = root.children[0];
  assert.equal(svg.tag, "svg");
  assert.equal(svg.children.filter(child => child.tag === "polygon").length, 1);
  assert.equal(svg.children.filter(child => child.tag === "line").length, 1);
});
