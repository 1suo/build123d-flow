# Minimal browser bootstrap for build123d-flow.
# The application renders tessellated meshes in Three.js and does not use
# build123d's optional ocp_vscode / ocp-tessellate presentation stack.

import micropip
import os
import json
from pathlib import Path

restored_environment = os.environ.get("BUILD123D_FLOW_ENV_RESTORED") == "1"

micropip.set_index_urls([])
OCP_VERSION = "8.0.1.1.post202609241841"

print("Preparing browser-compatible dependencies…")

# Pillow's native extension is unavailable in Pyodide. build123d only needs
# this small color parser in the paths used by this application.
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

# build123d imports this package for optional GLTF PBR material injection.
# The app has its own Three.js renderer; unsupported PBR export must fail
# explicitly rather than replacing or mutating upstream build123d behavior.
micropip.add_mock_package("threejs-materials", "1.2.5", modules={
    "threejs_materials": (
        "class PbrProperties: pass\n"
        "def inject_materials(*args, **kwargs):\n"
        "    raise NotImplementedError('GLTF PBR material injection is unavailable in build123d-flow')\n"
    ),
})

# lib3mf is unavailable in Pyodide. Register only an import-time compatibility
# namespace so build123d itself can load. Any attempted Lib3MF operation fails
# explicitly; never replace build123d.Mesher or any other upstream API.
micropip.add_mock_package("lib3mf", "2.4.1", modules={
    "lib3mf": (
        "from enum import Enum\n"
        "class _ModelUnit(Enum):\n"
        "    MicroMeter = 0\n    MilliMeter = 1\n    CentiMeter = 2\n"
        "    Inch = 3\n    Foot = 4\n    Meter = 5\n"
        "class _ObjectType(Enum):\n"
        "    Other = 0\n    Model = 1\n    Support = 2\n    SolidSupport = 3\n"
        "class _Unavailable:\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        raise NotImplementedError('Lib3MF operations are unavailable in the browser runtime')\n"
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
    print(f"Installing {len(wheel_paths)} pre-staged Python wheels…")
    await micropip.install(wheel_paths, deps=False)
    print("Installed pre-staged Python wheels")
else:
    print("Restored Python environment; verifying required packages…")

print("Importing build123d…")

# All PyPI-only dependencies and the OCP wasm wheel are staged locally.
# Pyodide-native dependencies are loaded from the self-hosted lockfile closure.

# Fail during environment setup, not later when the graph runtime imports build123d.
import build123d
import build123d.exporters

print("Imported build123d and exporters")
