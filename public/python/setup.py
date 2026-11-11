# Minimal browser bootstrap for build123d-flow.
# Unlike build123d-sandbox, this app renders tessellated meshes in Three.js
# and does not need ocp_vscode / ocp-tessellate.

import micropip
import importlib.metadata

micropip.set_index_urls(["https://pypi.org/simple"])

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

print("Downloading OCP.wasm and lib3mf wheels…")
await micropip.install([
    "cadquery-ocp-novtk-OCP.wasm==8.0.1.0.post202609202030",
    "lib3mf-OCP.wasm==2.5.0.post202609201851",
])

ocp_version = importlib.metadata.version("cadquery-ocp-novtk-OCP.wasm")
lib3mf_version = importlib.metadata.version("lib3mf-OCP.wasm")
micropip.add_mock_package("cadquery-ocp-novtk", ocp_version, modules={})
micropip.add_mock_package("cadquery-ocp", ocp_version, modules={})
micropip.add_mock_package("lib3mf", lib3mf_version, modules={})

print("Installing build123d dependencies…")
await micropip.install(["webcolors==24.8.0"])
await micropip.install(["build123d==0.13.0"])

print("Importing build123d…")
import build123d
print("build123d runtime initialized")
