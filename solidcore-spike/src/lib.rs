use serde_json::json;
use wasm_bindgen::prelude::*;

use solidcore_ops::{make_box, Body, BooleanOp};
use solidcore_tess::MeshTolerance;

#[wasm_bindgen]
pub fn smoke_test() -> Result<String, JsValue> {
    let a = make_box([0.0, 0.0, 0.0], [10.0, 10.0, 10.0])
        .map_err(js_error)?;
    let b = make_box([5.0, 0.0, 0.0], [10.0, 10.0, 10.0])
        .map_err(js_error)?;

    let result = solidcore_ops::boolean::boolean(a, b, BooleanOp::Union)
        .map_err(js_error)?
        .ok_or_else(|| JsValue::from_str("SolidCore returned an empty union"))?;

    mesh_summary(&result)
}

fn mesh_summary(body: &Body) -> Result<String, JsValue> {
    let (record, _) = body.to_record().map_err(js_error)?;
    let (plan, _stats) = solidcore_tess::plan(
        &record,
        &MeshTolerance::display(0.05),
        20.0,
    )
    .map_err(js_error)?;
    let (mesh, refused) = plan.evaluate();

    serde_json::to_string(&json!({
        "backend": "SolidCore",
        "operation": "union(box(10,10,10), translated_box(10,10,10))",
        "record": {
            "faces": record.faces.len(),
            "edges": record.edges.len(),
            "vertices": record.vertices.len()
        },
        "mesh": {
            "vertices": mesh.positions.len(),
            "triangles": mesh.triangles.len(),
            "edges": mesh.edges.len()
        },
        "refused_faces": refused.len()
    }))
    .map_err(js_error)
}

fn js_error(error: impl std::fmt::Display) -> JsValue {
    JsValue::from_str(&error.to_string())
}
