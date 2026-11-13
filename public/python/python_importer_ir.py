"""Kernel-neutral intermediate representation for Python expressions.

The frontend converts Python syntax into immutable, source-located IR records.
This module deliberately knows nothing about graph nodes or CAD kernels.
"""
from dataclasses import dataclass
import ast


@dataclass(frozen=True)
class SourceLocation:
    line: int
    column: int


@dataclass(frozen=True)
class Expr:
    location: SourceLocation


@dataclass(frozen=True)
class LiteralExpr(Expr):
    value: object


@dataclass(frozen=True)
class NameExpr(Expr):
    name: str


@dataclass(frozen=True)
class SequenceExpr(Expr):
    kind: str
    items: tuple


@dataclass(frozen=True)
class AttributeExpr(Expr):
    receiver: Expr
    name: str


@dataclass(frozen=True)
class KeywordArg:
    name: str | None
    value: Expr


@dataclass(frozen=True)
class CallExpr(Expr):
    function: Expr
    args: tuple
    keywords: tuple


@dataclass(frozen=True)
class SliceExpr(Expr):
    start: Expr | None
    stop: Expr | None
    step: Expr | None


@dataclass(frozen=True)
class SubscriptExpr(Expr):
    value: Expr
    index: Expr


@dataclass(frozen=True)
class UnaryExpr(Expr):
    operator: str
    operand: Expr


@dataclass(frozen=True)
class BinaryExpr(Expr):
    operator: str
    left: Expr
    right: Expr


_BINARY_OPERATORS = {
    ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul",
    ast.Div: "div", ast.FloorDiv: "floor_div", ast.Mod: "mod",
    ast.Pow: "pow", ast.BitAnd: "bit_and", ast.BitOr: "bit_or",
    ast.BitXor: "bit_xor",
}
_UNARY_OPERATORS = {
    ast.USub: "negate", ast.UAdd: "positive", ast.Invert: "invert",
}


def _location(node):
    return SourceLocation(getattr(node, "lineno", 1), getattr(node, "col_offset", 0))


def lower_expression(node):
    """Convert supported expression syntax to source-located, kernel-neutral IR."""
    loc = _location(node)
    if isinstance(node, ast.Constant):
        return LiteralExpr(loc, node.value)
    if isinstance(node, ast.Name):
        return NameExpr(loc, node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        return SequenceExpr(
            loc, "list" if isinstance(node, ast.List) else "tuple",
            tuple(lower_expression(item) for item in node.elts),
        )
    if isinstance(node, ast.Set):
        return SequenceExpr(loc, "set", tuple(lower_expression(item) for item in node.elts))
    if isinstance(node, ast.Attribute):
        return AttributeExpr(loc, lower_expression(node.value), node.attr)
    if isinstance(node, ast.Call):
        return CallExpr(
            loc, lower_expression(node.func),
            tuple(lower_expression(arg) for arg in node.args),
            tuple(KeywordArg(item.arg, lower_expression(item.value)) for item in node.keywords),
        )
    if isinstance(node, ast.Subscript):
        index = node.slice
        if isinstance(index, ast.Slice):
            index_ir = SliceExpr(
                _location(index),
                lower_expression(index.lower) if index.lower else None,
                lower_expression(index.upper) if index.upper else None,
                lower_expression(index.step) if index.step else None,
            )
        else:
            index_ir = lower_expression(index)
        return SubscriptExpr(loc, lower_expression(node.value), index_ir)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return UnaryExpr(loc, _UNARY_OPERATORS[type(node.op)], lower_expression(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        return BinaryExpr(
            loc, _BINARY_OPERATORS[type(node.op)],
            lower_expression(node.left), lower_expression(node.right),
        )
    raise ValueError("Unsupported expression '{}' at line {}".format(type(node).__name__, loc.line))
