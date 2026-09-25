"""Guards against function-local imports that shadow a module-level import.

`import re` inside romm_sync's inner `_do_sync` made `re` a local variable for
the whole function, so every RomM push that did not go through the PS2
folder-card branch died with "cannot access local variable 're'" at the later
`re.sub` call (100 failures in one sync on 2026-09-26).
"""
import ast
import pathlib

ROUTERS = pathlib.Path(__file__).resolve().parent.parent / "app" / "routers"


def _module_level_imports(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
    return names


def test_no_function_local_import_shadows_a_module_import():
    offenders = []
    for path in sorted(ROUTERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top = _module_level_imports(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        name = a.asname or a.name.split(".")[0]
                        if name in top:
                            offenders.append(f"{path.name}:{node.lineno} import {a.name} inside {fn.name}()")
    assert not offenders, "Function-local imports shadow module imports: " + "; ".join(offenders)
