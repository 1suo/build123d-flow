import runpy
import tempfile
import unittest
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement

MODULE = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "prepare-pyodide.py"))

class DependencyClosureTests(unittest.TestCase):
    def test_setup_roots_and_mock_distributions_are_derived(self):
        with tempfile.TemporaryDirectory() as directory:
            setup = Path(directory) / "setup.py"
            setup.write_text(
                'import micropip\n'
                'micropip.add_mock_package("lib3mf", "2.4.1")\n'
                'await micropip.install(["build123d==0.13.0"])\n'
            )
            roots, mocks = MODULE["setup_roots"](setup)
        self.assertEqual({r.name for r in roots}, {"micropip", "build123d"})
        self.assertEqual(mocks, {"lib3mf"})

    def test_transitive_pypi_dependency_stages_pyodide_package(self):
        class FixtureResolver(MODULE["Resolver"]):
            def pypi_requires(self, req):
                return {
                    "build123d": [
                        "svgpathtools>=1.5.1,<2",
                        "cadquery-ocp-novtk>=8,<8.1",
                        "lib3mf>=2.4.1; sys_platform != 'linux' or platform_machine != 'aarch64'",
                        "py-lib3mf>=2.4.1; sys_platform == 'linux' and platform_machine == 'aarch64'",
                    ],
                    "svgpathtools": ["svgwrite>=1.4"],
                }.get(req.name, [])

        lock = {
            "micropip": {"version": "0.9.0", "depends": []},
            "svgwrite": {"version": "1.4.3", "depends": ["python-dateutil"]},
            "python-dateutil": {"version": "2.9.0", "depends": []},
        }
        resolver = FixtureResolver(lock, {"cadquery-ocp-novtk", "lib3mf"})
        resolver.add(Requirement("micropip"))
        resolver.add(Requirement("build123d==0.13.0"))
        self.assertEqual(resolver.required, {"micropip", "svgwrite", "python-dateutil"})

if __name__ == "__main__":
    unittest.main()
