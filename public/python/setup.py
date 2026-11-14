# Minimal browser bootstrap for build123d-flow.
# Unlike build123d-sandbox, this app renders tessellated meshes in Three.js
# and does not need ocp_vscode / ocp-tessellate.

import micropip
import importlib.metadata
import os
import json
from pathlib import Path

restored_environment = os.environ.get('BUILD123D_FLOW_ENV_RESTORED') == '1'

micropip.set_index_urls([])
OCP_VERSION = "8.0.1.0.post202609202030"

print("Preparing browser-compatible dependencies…")

# Pillow's native extension is unavailable in Pyodide. build123d only needs
# this small color parser in the paths used by this app.
micropip.add_mock_package("pillow", "12.2.0", modules={
    "PIL": "",
    "PIL.Image": "",
    "PIL.ImageColor": (
        "def getrgb(color):\n"
        "    color = color.strip()\n"
        "    if color.startswith('#'):\n"
        "        h = color.lstrip('#')\n"
        "        if len(h) == 3:\n"
        "            h = ''.join(c * 2 for c in h)\n"
        "        return tuple(int(h[i:i+2], 16) for i in range(0, len(h), 2))\n"
        "    if color.startswith('rgb'):\n"
        "        nums = color[color.index('(') + 1:color.index(')')].split(',')\n"
        "        return tuple(int(float(n.strip().rstrip('%'))) for n in nums)\n"
        "    import webcolors\n"
        "    rgb = webcolors.name_to_rgb(color)\n"
        "    return (rgb.red, rgb.green, rgb.blue)\n"
    ),
})

# Re-register lightweight aliases on each interpreter start; micropip metadata
# is in-memory and must be recreated after restoring site-packages from IndexedDB.
micropip.add_mock_package("cadquery-ocp-novtk", OCP_VERSION, modules={})
micropip.add_mock_package("cadquery-ocp", OCP_VERSION, modules={})

# build123d imports this package only for optional GLTF PBR material injection.
# The app has its own Three.js renderer and does not expose build123d's GLTF
# material pipeline. Keep ordinary build123d imports lightweight; fail explicitly
# if that unsupported export path is invoked with PBR materials.
micropip.add_mock_package("threejs-materials", "1.2.5", modules={
    "threejs_materials": (
        "class PbrProperties: pass\n"
        "def inject_materials(*args, **kwargs):\n"
        "    raise NotImplementedError('GLTF PBR material injection is not available in build123d-flow')\n"
    ),
})

# build123d imports Mesher at package import time; 3MF is unused here, so
# register the same import-only stub each session and install it only once.
micropip.add_mock_package("lib3mf", "2.4.1", modules={
    "lib3mf": (
        "from enum import Enum\n"
        "class _ModelUnit(Enum):\n"
        "    MicroMeter = 0\n    MilliMeter = 1\n    CentiMeter = 2\n"
        "    Inch = 3\n    Foot = 4\n    Meter = 5\n"
        "class _ObjectType(Enum):\n"
        "    Other = 0\n    Model = 1\n    Support = 2\n    SolidSupport = 3\n"
        "class _Unavailable: pass\n"
        "class Lib3MF:\n"
        "    __file__ = '/lib3mf/__init__.py'\n"
        "    ModelUnit = _ModelUnit\n    ObjectType = _ObjectType\n"
        "    Wrapper = _Unavailable\n    MeshObject = _Unavailable\n"
        "    MeshObjectIterator = _Unavailable\n    ComponentsObject = _Unavailable\n"
        "    Position = _Unavailable\n    Triangle = _Unavailable\n    Color = _Unavailable\n"
    ),
})

if not restored_environment:
    manifest_path = Path("/python/wheels/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wheel_paths = [f"emfs:/python/wheels/{item['filename']}" for item in manifest]
    print(f"Installing {len(wheel_paths)} pre-staged PyPI wheels…")
    await micropip.install(wheel_paths, deps=False)
    print("Installed pre-staged PyPI wheels")
else:
    print("Restored Python environment; verifying required packages…")

print("Importing build123d…")

# All PyPI-only dependencies and the OCP wasm wheel are staged locally.
# Pyodide-native dependencies are loaded from the self-hosted lockfile closure.

# Fail during environment setup, not later when the graph runtime imports build123d.
import build123d
import build123d.exporters
print("Imported build123d and exporters")
