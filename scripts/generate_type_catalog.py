#!/usr/bin/env python3
"""Generate the versioned build123d API catalog from the installed real kernel."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "public" / "python"
sys.path.insert(0, str(PYTHON_DIR))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    import flow_runtime

    payload = json.loads(flow_runtime.catalog_bundle_json())
    if payload.get("build123d_version") != "0.13.0":
        raise SystemExit(
            "Expected build123d 0.13.0, discovered {}".format(
                payload.get("build123d_version")
            )
        )
    if not payload.get("types") or not payload.get("nodes"):
        raise SystemExit("Discovery produced an empty type or node registry")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        "Generated build123d {} catalog: {} types, {} nodes".format(
            payload["build123d_version"], len(payload["types"]), len(payload["nodes"])
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
