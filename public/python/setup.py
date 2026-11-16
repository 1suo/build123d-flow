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
OCP_VERSION = "8.0.1.1.post202609241841"
# The PyEmscripten build provides the upstream "lib3mf" import package.
PROVIDED_IMPORTS = {"lib3mf": "lib3mf-OCP.wasm==2.5.0.post202609241713"}

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
manifest_path = Path("/python/wheels/manifest.json")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
wheel_paths = [f"emfs:/python/wheels/{item['filename']}" for item in manifest]
if not restored_environment:
    print(f"Installing {len(wheel_paths)} pre-staged PyPI wheels…")
    await micropip.install(wheel_paths, deps=False)
    print("Installed pre-staged PyPI wheels")
else:
    print("Restored Python environment; verifying required packages…")

# Restored browser environments may predate the real Lib3MF wheel. Ensure the
# ABI-matched upstream binding is present instead of falling back to a stub.
lib3mf_requirement = PROVIDED_IMPORTS["lib3mf"]
lib3mf_version = lib3mf_requirement.split("==", 1)[1]
try:
    installed_lib3mf_version = importlib.metadata.version("lib3mf-OCP.wasm")
except importlib.metadata.PackageNotFoundError:
    installed_lib3mf_version = None
if installed_lib3mf_version != lib3mf_version:
    lib3mf_wheel = next(
        (item for item in manifest if item["name"].lower() == "lib3mf-ocp.wasm"),
        None,
    )
    if lib3mf_wheel is None:
        raise RuntimeError("The staged environment is missing the required Lib3MF WebAssembly wheel")
    await micropip.install(
        f"emfs:/python/wheels/{lib3mf_wheel['filename']}",
        deps=False,
    )
    installed_lib3mf_version = importlib.metadata.version("lib3mf-OCP.wasm")
if installed_lib3mf_version != lib3mf_version:
    raise RuntimeError(
        f"Expected Lib3MF {lib3mf_version}, found {installed_lib3mf_version}"
    )

print("Importing build123d…")

# All PyPI-only dependencies and the OCP wasm wheel are staged locally.
# Pyodide-native dependencies are loaded from the self-hosted lockfile closure.

# Fail during environment setup, not later when the graph runtime imports build123d.
import build123d
import build123d.exporters

# Use build123d's unmodified upstream Mesher backed by the real Lib3MF WASM binding.
import build123d.mesher
print("Imported build123d and exporters")
