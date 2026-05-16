from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from typing import Any


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ToolResult:
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class ToolGateway:
    def execute(self, tool_name: str, arguments: dict[str, Any], allowed_tools: list[str]) -> ToolResult:
        if tool_name not in allowed_tools:
            raise ToolError("TOOL_NOT_ALLOWED", f"Tool '{tool_name}' is not allowed for this agent")
        if tool_name == "calculator":
            expression = str(arguments.get("expression", "")).strip()
            if not expression:
                raise ToolError("TOOL_VALIDATION_FAILED", "Calculator expression is required")
            value = _safe_eval(expression)
            return ToolResult(
                tool_name="calculator",
                arguments={"expression": expression},
                result={"value": value, "text": str(value)},
            )
        raise ToolError("TOOL_UNAVAILABLE", f"Tool '{tool_name}' is not registered")


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval(expression: str) -> float | int:
    if len(expression) > 120:
        raise ToolError("TOOL_VALIDATION_FAILED", "Calculator expression is too long")
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError("TOOL_VALIDATION_FAILED", "Calculator expression is invalid") from exc
    return _eval_node(parsed.body)


def _eval_node(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINARY_OPERATORS:
            raise ToolError("TOOL_VALIDATION_FAILED", "Calculator operator is not allowed")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if op_type is ast.Pow and abs(right) > 8:
            raise ToolError("TOOL_VALIDATION_FAILED", "Calculator exponent is too large")
        return _BINARY_OPERATORS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPERATORS:
            raise ToolError("TOOL_VALIDATION_FAILED", "Calculator unary operator is not allowed")
        return _UNARY_OPERATORS[op_type](_eval_node(node.operand))
    raise ToolError("TOOL_VALIDATION_FAILED", "Calculator expression contains unsupported syntax")
