"""Stdin-code evaluator behind ``tinker`` — always run in its own process.

Reads the code from stdin (no argv size limits, nothing shell-quoted), execs
it with the trailing expression captured, and prints one JSON object to
stdout. The parent tool enforces the timeout and kills the process on expiry.
"""

from __future__ import annotations

import ast
import json
import sys
import traceback

_RESULT_NAME = "__fastplace_tinker_result__"


def _capture_trailing_expression(tree: ast.Module) -> bool:
    """Rewrite a trailing ``Expr`` into an assignment; True when captured."""
    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        return False
    last = tree.body[-1]
    tree.body[-1] = ast.copy_location(
        ast.Assign(
            targets=[ast.Name(id=_RESULT_NAME, ctx=ast.Store())],
            value=last.value,
        ),
        last,
    )
    ast.fix_missing_locations(tree)
    return True


def evaluate(code: str) -> str:
    """Run ``code``; return one JSON line with the outcome."""
    namespace: dict[str, object] = {"__name__": "__fastplace_tinker__"}
    try:
        tree = ast.parse(code)
        captures = _capture_trailing_expression(tree)
    except SyntaxError as exc:
        return json.dumps({"ok": False, "error": f"SyntaxError: {exc.msg}"})

    try:
        exec(compile(tree, "<tinker>", "exec"), namespace)  # noqa: S102 — by design
    except BaseException:
        exc_type, exc_value, _ = sys.exc_info()
        type_name = exc_type.__name__ if exc_type is not None else "Exception"
        return json.dumps(
            {
                "ok": False,
                "error": type_name + ": " + str(exc_value),
                "traceback": traceback.format_exc(),
            }
        )

    payload: dict[str, object] = {"ok": True}
    if captures:
        payload["repr"] = repr(namespace.get(_RESULT_NAME))
    return json.dumps(payload)


def main() -> int:
    code = sys.stdin.read()
    sys.stdout.write(evaluate(code) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
