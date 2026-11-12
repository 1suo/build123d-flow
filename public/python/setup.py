# Minimal browser bootstrap for build123d-flow.
# Unlike build123d-sandbox, this app renders tessellated meshes in Three.js
# and does not need ocp_vscode / ocp-tessellate.

import micropip
import importlib.metadata
import os

restored_environment = os.environ.get('BUILD123D_FLOW_ENV_RESTORED') == '1'

micropip.set_index_urls(["https://pypi.org/simple"])
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
    try:
        ocp_installed = importlib.metadata.version("cadquery-ocp-novtk-OCP.wasm") == OCP_VERSION
    except importlib.metadata.PackageNotFoundError:
        ocp_installed = False
    if not ocp_installed:
        print("Downloading OCP.wasm…")
        await micropip.install([f"cadquery-ocp-novtk-OCP.wasm=={OCP_VERSION}"])

    try:
        lib3mf_installed = importlib.metadata.version("lib3mf") == "2.4.1"
    except importlib.metadata.PackageNotFoundError:
        lib3mf_installed = False
    if not lib3mf_installed:
        await micropip.install(["lib3mf==2.4.1"])

    # Reconcile dependencies on every startup, including restored environments.
    # A build123d metadata entry alone does not prove its dependencies are present.
    print("Checking build123d dependencies…")
    await micropip.install([
        "webcolors==24.8.0",
        "typing_extensions>=4.16,<5",
        "numpy>=2,<3",
        "svgpathtools>=1.5.1,<2",
        "anytree>=2.8,<3",
        "ezdxf>=1.1,<2",
        "fonttools>=4.39,<5",
        "ipython>=8,<10",
        "ocpsvg>=0.7,<0.8",
        "ocp_gordon>=0.3.1,<0.4",
        "trianglesolver",
        "sympy",
        "scipy",
        "scikit-learn>=1.5,<2",
        "webcolors~=24.8.0",
        "requests>=2.32,<3",
        "bd_materials>=0.2,<0.3",
        "threejs-materials>=1.2.1,<1.3",
    ])

    try:
        build123d_installed = importlib.metadata.version("build123d") == "0.13.0"
    except importlib.metadata.PackageNotFoundError:
        build123d_installed = False

    if not build123d_installed:
        print("Installing build123d…")
        # Install build123d without its upstream dependency resolver: its platform marker
        # selects lib3mf on Pyodide/WASM even though this app never uses 3MF.
        await micropip.install(["build123d==0.13.0"], deps=False)
    else:
        print("Using restored build123d package")

else:
    print("Using verified persistent Python environment")

print("Importing build123d…")
import build123d
print("build123d runtime initialized")
