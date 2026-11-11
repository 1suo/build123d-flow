# Browser dependency bootstrap based on the working setup in
# jojain/build123d-sandbox. Keep the OCP wheel pins in sync with that project.
import micropip

micropip.set_index_urls(["https://pypi.org/simple"])

# Packages with native OS / C-extension requirements that are not needed by
# this browser-side modeling runtime.
micropip.add_mock_package("pyperclip", "1.9.0", modules={
    "pyperclip": "def copy(text): pass\ndef paste(): return ''",
})
micropip.add_mock_package("websockets", "16.0", modules={
    "websockets": "",
    "websockets.sync": "",
    "websockets.sync.client": "def connect(*args, **kwargs): pass",
    "websockets.exceptions": "class WebSocketException(Exception): pass",
})
micropip.add_mock_package("pillow", "12.2.0", modules={
    "PIL": "", "PIL.Image": "", "PIL.ImageColor": "",
})
micropip.add_mock_package("ipykernel", "6.29.5", modules={"ipykernel": ""})
micropip.add_mock_package("flask", "3.0.0", modules={"flask": ""})
micropip.add_mock_package("flask_sock", "0.7.0", modules={"flask_sock": ""})
micropip.add_mock_package("psutil", "7.2.2", modules={
    "psutil": (
        "class Process:\n"
        "    def __init__(self, *args, **kwargs): pass\n"
        "    def parent(self): return None\n"
        "    def name(self): return ''\n"
    )
})

print("Installing OCP.wasm and lib3mf wheels…")
await micropip.install([
    "cadquery-ocp-novtk-OCP.wasm==8.0.1.0.post202609202030",
    "lib3mf-OCP.wasm==2.5.0.post202609201851",
])

import importlib.metadata

ocp_version = importlib.metadata.version("cadquery-ocp-novtk-OCP.wasm")
lib3mf_version = importlib.metadata.version("lib3mf-OCP.wasm")
micropip.add_mock_package("cadquery-ocp-novtk", ocp_version, modules={})
micropip.add_mock_package("cadquery-ocp", ocp_version, modules={})
micropip.add_mock_package("lib3mf", lib3mf_version, modules={})

# Resolve the strict webcolors pin before micropip processes the looser
# ocp-tessellate dependency, matching the sandbox's known-good ordering.
await micropip.install(["webcolors==24.8.0"])
print("Installing ocp_vscode runtime dependencies and build123d…")
await micropip.install(["ocp_vscode==4.1.0", "build123d==0.13.0"])

import build123d
print("build123d runtime initialized")
