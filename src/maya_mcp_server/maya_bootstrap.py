"""Bootstrap code that runs in Maya to provide MCP server functionality.

This file is read and injected into Maya sessions to create a persistent
_mcp module with helper functions. It must be self-contained and use only
standard library imports that are available in Maya's Python environment.

The code is executed via: exec(open(__file__).read(), globals())
"""


def create_module(name: str, code: str, overwrite: bool = False) -> str:
    """Create a virtual module from source code."""
    import json
    import sys
    import types

    parts = name.split(".")

    # Validate module name components
    for part in parts:
        if not part.isidentifier():
            return json.dumps(
                {
                    "error": {
                        "code": "invalid_module_name",
                        "message": f"Invalid module name component '{part}' in '{name}'",
                    }
                }
            )

    # Create parent packages as needed
    for i in range(len(parts) - 1):
        parent_name = ".".join(parts[: i + 1])
        if parent_name not in sys.modules:
            parent_mod = types.ModuleType(parent_name)
            setattr(parent_mod, "__path__", [])  # Package marker
            sys.modules[parent_name] = parent_mod
        else:
            # Ensure existing parent is marked as a package
            parent_mod = sys.modules[parent_name]
            if not hasattr(parent_mod, "__path__"):
                setattr(parent_mod, "__path__", [])

    if name in sys.modules and not overwrite:
        return json.dumps(
            {
                "error": {
                    "code": "module_exists",
                    "message": f"Module '{name}' already exists. Use overwrite=True.",
                }
            }
        )

    module = types.ModuleType(name)
    module.__file__ = f"<mcp:{name}>"
    compiled = compile(code, module.__file__, "exec")
    try:
        # Execute in module namespace (standard initialization)
        exec(compiled, module.__dict__)
    except Exception as e:
        return json.dumps(
            {
                "error": {
                    "code": "module_create_failed",
                    "message": (
                        f"Failed to compile/execute module '{name}': {type(e).__name__}: {e}"
                    ),
                }
            }
        )
    # Teardown protocol (upstream #4): replacing the sys.modules
    # entry would orphan the old module's globals - give it a chance to
    # release resources (e.g. a listening QtCommandServer) first. A failing
    # hook must not block the replacement; it surfaces as a warning.
    old_module = sys.modules.get(name)
    teardown_warning = None
    if overwrite and old_module is not None:
        teardown = getattr(old_module, "_mcp_teardown", None)
        if callable(teardown):
            try:
                teardown()
            except Exception as e:
                teardown_warning = f"_mcp_teardown of '{name}' raised {type(e).__name__}: {e}"
    sys.modules[name] = module

    # Walk up the tree and ensure each parent references its child.
    # This handles both fresh parents (missing child attr) and stale
    # references from previous create_module calls.
    for i in range(len(parts) - 1, 0, -1):
        parent_name = ".".join(parts[:i])
        child_name = ".".join(parts[: i + 1])
        if parent_name in sys.modules:
            setattr(sys.modules[parent_name], parts[i], sys.modules[child_name])

    result = {"success": True, "message": f"Module '{name}' created"}
    if teardown_warning is not None:
        result["warning"] = teardown_warning
    return json.dumps(result)
