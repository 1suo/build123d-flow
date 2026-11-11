# Browser dependency bootstrap adapted from jojain/build123d-sandbox.
# Keep versions aligned with the tested OCP.wasm wheel set.
import micropip

micropip.set_index_urls(["https://pypi.org/simple"])

print("Installing OCP.wasm wheels...")
await micropip.install([
    "cadquery-ocp-novtk-OCP.wasm==8.0.1.0.post202609202030",
    "lib3mf-OCP.wasm==2.5.0.post202609201851",
])

import importlib.metadata

ocp_version = importlib.metadata.version("cadquery-ocp-novtk-OCP.wasm")
lib3mf_version = importlib.metadata.version("lib3mf-OCP.wasm")

# Satisfy build123d's native distribution dependency names without replacing
# the OCP / lib3mf modules installed by the WASM wheels.
micropip.add_mock_package("cadquery-ocp-novtk", ocp_version, modules={})
micropip.add_mock_package("cadquery-ocp", ocp_version, modules={})
micropip.add_mock_package("lib3mf", lib3mf_version, modules={})

await micropip.install(["webcolors==24.8.0"])
await micropip.install(["build123d==0.13.0"])

import build123d
print(f"build123d {build123d.__version__ if hasattr(build123d, '__version__') else 'loaded'}")
