"""Tests for source-located, kernel-neutral Python expression IR."""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "public" / "python"))
from python_importer_ir import (
    AttributeExpr, BinaryExpr, CallExpr, LiteralExpr, NameExpr,
    SequenceExpr, SliceExpr, SubscriptExpr, UnaryExpr, lower_expression,
)


class PythonImporterIRTests(unittest.TestCase):
    def test_literals_and_names_preserve_values_and_locations(self):
        literal = lower_expression(ast.parse("42").body[0].value)
        name = lower_expression(ast.parse("part").body[0].value)
        self.assertEqual(literal, LiteralExpr(type(literal.location)(1, 0), 42))
        self.assertIsInstance(name, NameExpr)
        self.assertEqual(name.name, "part")

    def test_nested_collections_are_structural_ir(self):
        value = lower_expression(ast.parse("[1, (2, 3)]").body[0].value)
        self.assertIsInstance(value, SequenceExpr)
        self.assertEqual(value.kind, "list")
        self.assertEqual(value.items[1].kind, "tuple")

    def test_calls_attributes_and_keyword_arguments_are_structural(self):
        value = lower_expression(ast.parse("part.cut(tool, clean=True)").body[0].value)
        self.assertIsInstance(value, CallExpr)
        self.assertIsInstance(value.function, AttributeExpr)
        self.assertEqual(value.function.name, "cut")
        self.assertEqual(value.keywords[0].name, "clean")

    def test_subscript_slice_and_operators_are_structural(self):
        subscript = lower_expression(ast.parse("items[1:stop:2]").body[0].value)
        self.assertIsInstance(subscript, SubscriptExpr)
        self.assertIsInstance(subscript.index, SliceExpr)
        binary = lower_expression(ast.parse("a + -b").body[0].value)
        self.assertIsInstance(binary, BinaryExpr)
        self.assertIsInstance(binary.right, UnaryExpr)

    def test_unsupported_expression_fails_at_frontend_boundary(self):
        with self.assertRaisesRegex(ValueError, "Unsupported expression"):
            lower_expression(ast.parse("lambda x: x").body[0].value)


if __name__ == "__main__":
    unittest.main()
