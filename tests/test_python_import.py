"""Static Python-to-graph import tests."""

import pytest

import flow_runtime


def test_import_generated_style_build123d_calls():
    graph = flow_runtime.import_python_code(
        "import build123d as b3d\n"
        "from build123d import Box as _api_callable_0\n"
        "_node_1 = _api_callable_0(length=20, width=30, height=10)\n"
        "result = _node_1\n"
    )
    assert len(graph["nodes"]) == 1
    node = graph["nodes"][0]
    assert node["data"]["definition"]["name"] == "Box"
    assert node["data"]["params"]["length"] == 20
    assert node["data"]["params"]["width"] == 30
    assert node["data"]["params"]["height"] == 10
    assert graph["edges"] == []


def test_import_rejects_control_flow_without_execution():
    with pytest.raises(ValueError, match="Unsupported statement.*For"):
        flow_runtime.import_python_code(
            "from build123d import Box\n"
            "for index in range(3):\n"
            "    Box(10, 10, 10)\n"
        )


def test_import_reports_syntax_error():
    with pytest.raises(ValueError, match="Python syntax error"):
        flow_runtime.import_python_code("from build123d import\n")


def test_import_rejects_non_build123d_imports():
    with pytest.raises(ValueError, match="Only build123d imports"):
        flow_runtime.import_python_code("import os\n")
