# --- _mcp_scene introspection fragment: own file, same injection unit ---
#
# NOT a standalone injected module: scene_tools._ensure_module_injected
# concatenates this file onto maya_scene_module.py and injects the
# assembly as _mcp_scene - same module name, same trigger, same failure
# domain (the first T-07 migration unit). Self-contained on purpose: it
# references nothing private in the monolith.
#
# Runtime floor: Maya >=2023 (Python 3.9). This file carries NO
# __future__ import of its own (it would sit mid-file in the
# concatenated payload = SyntaxError) - the unquoted PEP-604
# annotations below are py3.9-safe ONLY because the monolith's
# `from __future__ import annotations` (maya_scene_module.py:13)
# governs the assembled unit and makes every annotation lazy.
# Do NOT exec/import this file standalone on py3.9.

from typing import Any

import maya.cmds as cmds


# ---- domain errors ----------------------------------------------------


def _int_err(code: str, message: str, suggestion: str | None = None) -> dict[str, Any]:
    """Domain error result; same envelope as the monolith's _err but kept
    separate so this fragment stays self-contained."""
    err: dict[str, Any] = {"code": code, "message": message}
    if suggestion:
        err["suggestion"] = suggestion
    return {"error": err}


def _scalar(v: Any) -> Any:
    """Unwrap single-item lists from attributeQuery value flags."""
    if isinstance(v, (list, tuple)) and len(v) == 1:
        return v[0]
    return v


def _jsonable(v: Any) -> Any:
    """Coerce getAttr output to JSON-safe values (tuples -> lists)."""
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


# Single-flag attributeQuery: returns None on failure (honest "unknown").
def _aq(node: str, attr: str, flag: str) -> Any:
    try:
        kwarg: dict[str, Any] = {flag: True}
        return cmds.attributeQuery(attr, node=node, **kwarg)
    except Exception:
        return None


_ATTR_FACET_FLAGS = (
    ("readable", "readable"),
    ("writable", "writable"),
    ("connectable", "connectable"),
    ("keyable", "keyable"),
    ("multi", "multi"),
    ("hidden", "hidden"),
    ("storable", "storable"),
    ("index_matters", "indexMatters"),
)


# Response-size caps: the attrs and connections faces each default
# to a soft cap of _DESCRIBE_SOFT_CAP items; a caller may explicitly relax
# via limit= up to _DESCRIBE_HARD_CAP (same idiom as scene_nodes
# limit=50/cap=100). Truncation is disclosed via *_truncated flags plus
# totals - never silent. The caps bound RESPONSE SIZE only: the ~10
# attributeQuery calls per attribute are a separate main-thread cost axis
# inside Maya - if real-Maya timing proves problematic that is a distinct
# profiling/batched-query debt, to be opened when something is measured,
# not a reason to widen these caps. The two caps are constants on purpose:
# no env or config knob, and raising one is a decision - they are inferred,
# not measured.
_DESCRIBE_SOFT_CAP = 200
_DESCRIBE_HARD_CAP = 1000


def _describe_attr(node: str, attr: str, include_values: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": attr}
    if not _aq(node, attr, "exists"):
        entry["exists"] = False
        return entry
    entry["exists"] = True
    for field, flag in _ATTR_FACET_FLAGS:
        v = _aq(node, attr, flag)
        entry[field] = bool(v) if v is not None else None
    children = _aq(node, attr, "listChildren")
    entry["children"] = list(children) if children else []
    enum_vals = _aq(node, attr, "listEnum")
    entry["enum"] = bool(_aq(node, attr, "enum") or enum_vals)
    entry["enum_values"] = list(enum_vals) if enum_vals else []
    try:
        entry["attr_type"] = cmds.getAttr(node + "." + attr, type=True)
    except Exception:
        entry["attr_type"] = None
    entry["min"] = _scalar(_aq(node, attr, "minimum")) if _aq(node, attr, "minExists") else None
    entry["max"] = _scalar(_aq(node, attr, "maximum")) if _aq(node, attr, "maxExists") else None
    try:
        entry["locked"] = bool(cmds.getAttr(node + "." + attr, lock=True))
    except Exception:
        entry["locked"] = None
    if include_values:
        entry["soft_min"] = (
            _scalar(_aq(node, attr, "softMin")) if _aq(node, attr, "softMinExists") else None
        )
        entry["soft_max"] = (
            _scalar(_aq(node, attr, "softMax")) if _aq(node, attr, "softMaxExists") else None
        )
        try:
            entry["value"] = _jsonable(cmds.getAttr(node + "." + attr))
        except Exception as e:
            entry["value"] = None
            entry["value_error"] = f"{type(e).__name__}: {e}"
    return entry


def _node_connections(node: str) -> list[dict[str, str]]:
    """Connection wiring as {src_plug, dst_plug, direction}.

    listConnections(connections=True) returns (plug on queried node,
    remote plug) pairs; source=True selects connections where the node is
    the destination (incoming), destination=True the reverse.
    """
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for direction, want_src, want_dst in (("in", True, False), ("out", False, True)):
        pairs = (
            cmds.listConnections(
                node,
                plugs=True,
                connections=True,
                source=want_src,
                destination=want_dst,
            )
            or []
        )
        it = iter(pairs)
        for local, remote in zip(it, it):
            if direction == "in":
                rec = {"src_plug": remote, "dst_plug": local, "direction": "in"}
            else:
                rec = {"src_plug": local, "dst_plug": remote, "direction": "out"}
            key = (rec["src_plug"], rec["dst_plug"])
            if key not in seen:
                seen.add(key)
                out.append(rec)
    return out


# ---- public surface (called by host tools) ----------------------------


def describe_node(
    node: str,
    attrs: list[str] | None = None,
    include_values: bool = False,
    include_connections: bool = True,
    limit: int = _DESCRIBE_SOFT_CAP,
) -> dict[str, Any]:
    """Instance-level API self-description of one node.

    Returns {node, type, attrs[{name, exists, attr_type, readable,
    writable, connectable, keyable, multi, hidden, locked, storable,
    children, index_matters, enum, enum_values, min, max, (+ soft_min,
    soft_max, value)}], attrs_truncated, total_count, connections?[
    {src_plug, dst_plug, direction}], connections_truncated?,
    total_connections?}. Response-size budget: each face returns at most
    limit items (soft default 200, caller-relaxable up to the hard cap
    1000); over-budget output is truncated and disclosed via
    *_truncated + totals - never silent. Consumers narrow and re-query
    (an explicit attrs=[...] list is the natural continuation form;
    connections have no cursor in v1).
    Sparse shape: an attr that fails its own exists check (listAttr vs
    attributeQuery disagreement / TOCTOU edge) carries only
    {name, exists: False} - facets are never fabricated.
    """
    try:
        if not isinstance(node, str) or not node.strip():
            return _int_err("node_not_found", "node must be a non-empty node name")
        if not cmds.objExists(node):
            return _int_err(
                "node_not_found",
                f"no such node: {node}",
                "enumerate names with scene_nodes()",
            )
        if attrs is not None:
            if not isinstance(attrs, (list, tuple)) or not all(isinstance(a, str) for a in attrs):
                return _int_err("query_failed", "attrs must be a list of attribute names")
            missing = [a for a in attrs if not _aq(node, a, "exists")]
            if missing:
                return _int_err(
                    "attr_not_found",
                    f"{node} has no attribute(s): {', '.join(missing)}",
                    "call scene_describe without attrs to list them all",
                )
            names = list(attrs)
        else:
            names = cmds.listAttr(node) or []
        limit = min(max(int(limit), 1), _DESCRIBE_HARD_CAP)
        total = len(names)
        names = names[:limit]
        result = {
            "node": node,
            "type": cmds.objectType(node),
            "attrs": [_describe_attr(node, a, include_values) for a in names],
            "attrs_truncated": total > len(names),
            "total_count": total,
        }
        if include_connections:
            conns = _node_connections(node)
            result["connections"] = conns[:limit]
            result["connections_truncated"] = len(conns) > limit
            result["total_connections"] = len(conns)
        return result
    except Exception as e:
        return _int_err("query_failed", f"{type(e).__name__}: {e}")


def list_nodes(
    node_type: str | None = None,
    pattern: str | None = None,
    dag_only: bool = False,
    inherited: bool = True,
    limit: int = 50,
    cursor: str | None = None,
    include_type_counts: bool = False,
) -> dict[str, Any]:
    """Bounded enumeration of scene nodes incl. non-DAG.

    ls(type=) is inheritance-aware in Maya; exactType= pins exact type.
    The cursor is a positional offset into the deterministic sorted
    match set - valid while the scene is unchanged.
    """
    try:
        kw: dict[str, Any] = {"long": True}
        if dag_only:
            kw["dagObjects"] = True
        if node_type:
            kw["type" if inherited else "exactType"] = node_type
        if pattern:
            names = cmds.ls(pattern, **kw) or []
        else:
            names = cmds.ls(**kw) or []
        names = sorted(set(names))
        total = len(names)
        offset = 0
        if cursor is not None:
            try:
                offset = int(cursor)
            except (TypeError, ValueError):
                return _int_err(
                    "invalid_cursor",
                    f"cursor {cursor!r} is not a valid page token",
                )
            if offset < 0 or offset > total:
                return _int_err(
                    "invalid_cursor",
                    f"cursor {cursor!r} out of range (total={total})",
                    "scene may have changed; restart without cursor",
                )
        limit = min(max(int(limit), 1), 100)
        page = names[offset : offset + limit]
        has_more = offset + limit < total
        result = {
            "total_count": total,
            "count": len(page),
            "nodes": page,
            "has_more": has_more,
            "next_cursor": str(offset + limit) if has_more else None,
            "limit": limit,
        }
        if include_type_counts:
            counts: dict[str, int] = {}
            for n in names:
                t = cmds.nodeType(n)
                counts[t] = counts.get(t, 0) + 1
            result["type_counts"] = counts
        return result
    except Exception as e:
        return _int_err("query_failed", f"{type(e).__name__}: {e}")
