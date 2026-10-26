"""Poly Haven asset client - host-side search/download/cache.

Maya has zero network surface: this module is the HOST half of the
asset pipeline. It queries api.polyhaven.com, downloads FBX + texture
files from the Poly Haven CDN into a platformdirs cache, verifies size
and the official per-file md5 (PH publishes md5 in /files/{id}), and
returns a validated asset_descriptor - the handoff contract the
Maya-side importer (asset_module / _mcp_asset) consumes.

Guardrails (safety net, not a boundary - threat-model.md):
  * https-only, host whitelist (api.polyhaven.com + dl.polyhaven.org/.com)
  * mandatory User-Agent (Poly Haven API requirement)
  * per-file + total size caps, timeouts
  * no silent stale cache: metadata is always re-fetched; when the
    network is down the caller gets an explicit network_unavailable
    domain error even if the cache is complete
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

API_BASE = "https://api.polyhaven.com"
ALLOWED_HOSTS = frozenset(
    {
        "api.polyhaven.com",
        # Observed CDN host (the design listed dl.polyhaven.com; the
        # API actually returns dl.polyhaven.org URLs - both whitelisted).
        "dl.polyhaven.org",
        "dl.polyhaven.com",
    }
)
USER_AGENT = "mcp-for-maya (+https://github.com/Xxx91n/mcp-for-maya)"

DEFAULT_TIMEOUT_S = 30.0
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
SEARCH_LIMIT_MAX = 20  # result list capped, total_count preserved
SEARCH_INDEX_TTL_S = 300.0  # /assets index is multi-MB; TTL-bound reuse

RESOLUTIONS = ("1k", "2k", "4k", "8k")
DEFAULT_RESOLUTION = "1k"

# Top-level /files keys that are geometry payloads, not textures.
_MODEL_KEYS = frozenset({"fbx", "blend", "gltf", "usd", "bto"})

# PH texture keys are "<part>_<suffix>" where <part> is the material
# name (PH model standard: texture prefix == material name). Multi-token
# suffixes (nor_gl) must be tried before single-token ones.
_SUFFIXES = (
    "nor_gl",
    "nor_dx",
    "roughness",
    "metallic",
    "displacement",
    "translucency",
    "specular",
    "emissive",
    "rough",
    "metal",
    "diff",
    "albedo",
    "spec",
    "disp",
    "arm",
    "ao",
    "bump",
    "coat",
    "sheen",
    "emis",
    "opacity",
)

_FMT_ORDER = ("jpg", "png", "exr", "hdr", "tif", "tiff")
_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class AssetError(Exception):
    """Domain failure inside the asset pipeline.

    Tools surface it as {"error": {code, message, suggestion}} - a
    domain result, never an MCP isError (explicit
    network_unavailable, no silent stale cache).
    """

    def __init__(self, code: str, message: str, suggestion: str | None = None) -> None:
        self.code = code
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.suggestion:
            err["suggestion"] = self.suggestion
        return {"error": err}


def validate_asset_id(asset_id: str) -> str:
    """Validate a Poly Haven asset id (slug)."""
    if not isinstance(asset_id, str) or not _ASSET_ID_RE.match(asset_id):
        raise AssetError(
            "invalid_asset_id",
            f"invalid asset id {asset_id!r}: expected a slug like 'Camera_01'",
        )
    return asset_id


def _as_int(value: Any, what: str) -> int:
    """API-supplied integer fields: dirty values are domain errors,
    never bare ValueError escaping the AssetError contract."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise AssetError("bad_response", f"non-integer {what}: {value!r}") from None


# TTL cache for the multi-MB /assets index — bounds call rate against
# the CC0 API. Per asset_type; payload dicts only.
_index_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.netloc or "").lower()
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS:
        raise AssetError(
            "url_not_allowed",
            f"refused URL outside whitelist: {parsed.scheme}://{host}",
            suggestion="only https on api.polyhaven.com / dl.polyhaven.org / dl.polyhaven.com",
        )


class _WhitelistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every 30x Location target against the whitelist.

    urllib follows redirects implicitly; without this a whitelisted URL
    could bounce to an arbitrary host (or downgrade https -> http) and
    still fetch. Each hop runs the same scheme+host check, hops capped.
    """

    max_redirections = 3

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_WhitelistRedirectHandler())


# Indirection so tests can fake the network at one seam (kept compatible
# with the urlopen(req, timeout=...) signature).
def _urlopen(req: urllib.request.Request, timeout: float) -> Any:
    return _OPENER.open(req, timeout=timeout)


def _fetch(url: str, *, timeout: float, max_bytes: int) -> bytes:
    """HTTPS GET against the whitelist with UA + size cap."""
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _urlopen(req, timeout=timeout) as resp:
            _check_url(resp.geturl() or url)
            length = resp.headers.get("Content-Length")
            if length is not None and _as_int(length, "Content-Length") > max_bytes:
                raise AssetError(
                    "download_too_large",
                    f"{url} declares {length} bytes, cap is {max_bytes}",
                )
            data: bytes = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise AssetError(
                    "download_too_large",
                    f"{url} exceeded the {max_bytes}-byte cap mid-read",
                )
            return data
    except AssetError:
        raise
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise AssetError(
                "asset_not_found",
                f"404 from {urllib.parse.urlsplit(url).netloc}: {url}",
                suggestion="check the asset id via asset_search (ids are case-sensitive)",
            ) from e
        raise AssetError("http_error", f"HTTP {e.code} from {url}: {e.reason}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise AssetError(
            "network_unavailable",
            f"cannot reach {urllib.parse.urlsplit(url).netloc}: {e}",
            suggestion="network down; cache is only served after fresh metadata verifies it",
        ) from e


def _fetch_json(url: str, timeout: float) -> Any:
    raw = _fetch(url, timeout=timeout, max_bytes=64 * 1024 * 1024)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise AssetError("bad_response", f"non-JSON response from {url}") from e


def search_assets(
    query: str = "",
    asset_type: str = "models",
    limit: int = SEARCH_LIMIT_MAX,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Search the Poly Haven asset index. Read-only; needs no Maya session.

    The /assets index payload is multi-MB, so the raw index is cached
    for SEARCH_INDEX_TTL_S (5 min) per asset_type — this is a
    listing cache, unrelated to the download path's fresh-metadata
    revalidation rule. Filtering/scoring always re-runs on the cached
    payload, so `query`/`limit` vary freely within the TTL.

    Returns {"results": [...<=limit], "total_count": <all matches>}.
    """
    limit = min(max(1, int(limit)), SEARCH_LIMIT_MAX)
    url = f"{API_BASE}/assets?t={urllib.parse.quote(str(asset_type))}"
    cached = _index_cache.get(str(asset_type))
    if cached is not None and time.monotonic() - cached[0] < SEARCH_INDEX_TTL_S:
        data = cached[1]
    else:
        data = _fetch_json(url, timeout)
        if not isinstance(data, dict):
            raise AssetError("bad_response", "unexpected /assets payload shape")
        _index_cache[str(asset_type)] = (time.monotonic(), data)

    tokens = [t for t in re.split(r"\s+", str(query).strip().lower()) if t]
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for asset_id, meta in data.items():
        if not isinstance(meta, dict):
            continue
        hay_name = f"{asset_id} {meta.get('name', '')}".lower()
        hay_tags = [str(t).lower() for t in meta.get("tags") or []]
        hay_cats = [str(c).lower() for c in meta.get("categories") or []]
        score = 0
        matched = True
        for tok in tokens:
            if tok in hay_name:
                score += 2
            elif any(tok in t for t in hay_tags) or any(tok in c for c in hay_cats):
                score += 1
            else:
                matched = False
                break
        if matched:
            scored.append((score, asset_id, meta))
    scored.sort(key=lambda m: (-m[0], m[1]))

    results = [
        {
            "id": aid,
            "name": m.get("name", aid),
            "categories": m.get("categories", []),
            "tags": (m.get("tags") or [])[:10],
        }
        for _s, aid, m in scored[:limit]
    ]
    return {
        "results": results,
        "total_count": len(scored),
        "limit": limit,
        "query": query,
        "asset_type": asset_type,
        "source": "api.polyhaven.com",
    }


def get_asset_files(asset_id: str, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Fetch the /files/{id} payload (per-key resolution/format tree)."""
    validate_asset_id(asset_id)
    data = _fetch_json(f"{API_BASE}/files/{urllib.parse.quote(asset_id)}", timeout)
    if not isinstance(data, dict) or not data:
        raise AssetError("bad_response", f"empty /files payload for {asset_id!r}")
    return data


_BARE_SUFFIX_ALIASES = {
    # Single-part assets ship bare map names ("Diffuse", "Rough",
    # "Metal") instead of "<part>_<suffix>" keys. Case-insensitive.
    "diffuse": "diff",
    "basecolor": "diff",
    "base_color": "diff",
    "metalness": "metallic",
    "normal": "normal",
    "alpha": "opacity",
}


def split_texture_key(key: str) -> tuple[str, str] | None:
    """Split 'body_nor_gl' -> ('body', 'nor_gl'); None if not a texture key.

    Single-part assets use bare map names ("Diffuse", "nor_gl") - these
    return part "" so select_files groups them into one part; the
    importer's single-part path then wires them to the mesh material.
    """
    lowered = key.lower()
    for suffix in _SUFFIXES:
        tail = "_" + suffix
        if lowered.endswith(tail) and len(key) > len(tail):
            return key[: -len(tail)], suffix
    bare = lowered
    if bare in _SUFFIXES:
        return "", bare
    if bare in _BARE_SUFFIX_ALIASES:
        return "", _BARE_SUFFIX_ALIASES[bare]
    return None


def _pick_entry(variants: Any, resolution: str) -> tuple[str, str, dict[str, Any]] | None:
    """Pick (resolution, fmt, entry): requested res first, then lower res."""
    if not isinstance(variants, dict):
        return None
    order = [resolution]
    if resolution in RESOLUTIONS:
        idx = RESOLUTIONS.index(resolution)
        order += [r for r in RESOLUTIONS[:idx][::-1]]
    order += [r for r in RESOLUTIONS if r not in order]
    order += [r for r in variants if r not in order]
    for res in order:
        fmts = variants.get(res)
        if not isinstance(fmts, dict):
            continue
        for fmt in _FMT_ORDER:
            entry = fmts.get(fmt)
            if isinstance(entry, dict) and isinstance(entry.get("url"), str):
                return res, fmt, entry
        for fmt, entry in fmts.items():
            if isinstance(entry, dict) and isinstance(entry.get("url"), str):
                return res, str(fmt), entry
    return None


def select_files(files: dict[str, Any], resolution: str) -> dict[str, Any]:
    """Choose the FBX + texture entries for one resolution."""
    fbx = _pick_entry(files.get("fbx"), resolution)
    if fbx is None:
        raise AssetError(
            "not_a_model",
            "asset has no FBX payload (not a model, or resolution missing)",
            suggestion="asset_import handles models only; pick an asset with an FBX file",
        )
    parts: dict[str, dict[str, Any]] = {}
    for key, variants in files.items():
        if key in _MODEL_KEYS:
            continue
        split = split_texture_key(key)
        if split is None:
            continue
        part, suffix = split
        picked = _pick_entry(variants, resolution)
        if picked is None:
            continue
        res, fmt, entry = picked
        parts.setdefault(part, {})[suffix] = {
            "url": entry["url"],
            "size": entry.get("size"),
            "md5": entry.get("md5"),
            "fmt": fmt,
            "resolution": res,
        }
    return {
        "fbx": {
            "url": fbx[2]["url"],
            "size": fbx[2].get("size"),
            "md5": fbx[2].get("md5"),
            "resolution": fbx[0],
            "fmt": fbx[1],
        },
        "parts": parts,
    }


def _sanitize_filename(url: str) -> str:
    """Last URL path segment, sanitized (no traversal, no weird chars)."""
    name = Path(urllib.parse.urlsplit(url).path).name
    name = _FILENAME_SAFE_RE.sub("_", name)
    if not name or name.startswith("."):
        name = "file_" + hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return name


def cache_dir_for(asset_id: str, resolution: str, cache_root: str | None = None) -> Path:
    """platformdirs user cache dir: <cache>/mcp-for-maya/polyhaven/<id>/<res>/."""
    if cache_root is not None:
        root = Path(cache_root)
    else:
        import platformdirs

        root = Path(platformdirs.user_cache_dir("mcp-for-maya")) / "polyhaven"
    return root / asset_id / resolution


def _hash_file(path: Path) -> tuple[str, str, int]:
    """One-pass (md5, sha256, size) - md5 verifies vs the API, sha256 audits."""
    md5 = hashlib.md5()
    sha = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            md5.update(chunk)
            sha.update(chunk)
            size += len(chunk)
    return md5.hexdigest(), sha.hexdigest(), size


def _verify_local(path: Path, size: Any, md5: Any) -> bool:
    """Cache validation against API-declared size + md5."""
    if not path.is_file():
        return False
    if size is not None and path.stat().st_size != _as_int(size, "declared size"):
        return False
    if md5:
        got_md5, _sha, _sz = _hash_file(path)
        if got_md5 != md5:
            return False
    return True


def download_asset(
    asset_id: str,
    resolution: str = DEFAULT_RESOLUTION,
    cache_root: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> dict[str, Any]:
    """Download (or verified-cache) an asset; returns the asset_descriptor.

    Always re-fetches /files metadata first - a stale cache is never
    served silently; with the network down this raises AssetError(
    'network_unavailable') even when every file is cached.
    """
    validate_asset_id(asset_id)
    if resolution not in RESOLUTIONS:
        raise AssetError(
            "invalid_resolution",
            f"resolution {resolution!r} not in {RESOLUTIONS}",
        )

    files = get_asset_files(asset_id, timeout=timeout)
    sel = select_files(files, resolution)

    entries: list[dict[str, Any]] = [{"role": "fbx", **sel["fbx"]}]
    for part, maps in sel["parts"].items():
        for suffix, e in maps.items():
            entries.append({"role": "texture", "part": part, "suffix": suffix, **e})

    total = sum(_as_int(e.get("size") or 0, "file size") for e in entries)
    if total > max_total_bytes:
        raise AssetError(
            "download_too_large",
            f"{total} bytes total exceeds the {max_total_bytes}-byte cap",
        )

    dest_dir = cache_dir_for(asset_id, resolution, cache_root)
    dest_dir_resolved = dest_dir.resolve()
    used: set[str] = set()
    downloaded_any = False
    manifest_files: dict[str, dict[str, Any]] = {}
    texture_parts: dict[str, dict[str, str]] = {}
    fbx_path = ""

    for e in entries:
        fname = _sanitize_filename(e["url"])
        while fname in used:
            fname = "_" + fname
        used.add(fname)
        target = dest_dir / fname
        if target.resolve().parent != dest_dir_resolved:
            raise AssetError(
                "path_escape",
                f"refused write outside cache dir: {fname!r}",
            )
        if not _verify_local(target, e.get("size"), e.get("md5")):
            data = _fetch(e["url"], timeout=timeout, max_bytes=max_file_bytes)
            if e.get("size") is not None and len(data) != _as_int(e["size"], "declared size"):
                raise AssetError(
                    "download_corrupt",
                    f"size mismatch for {fname}: {len(data)} != {e['size']}",
                )
            if e.get("md5") and hashlib.md5(data).hexdigest() != e["md5"]:
                raise AssetError("download_corrupt", f"md5 mismatch for {fname}")
            dest_dir.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            downloaded_any = True
        md5, sha, size = _hash_file(target)
        manifest_files[fname] = {
            "url": e["url"],
            "size": size,
            "md5": md5,
            "sha256": sha,
        }
        if e["role"] == "fbx":
            fbx_path = str(target)
        else:
            texture_parts.setdefault(e["part"], {})[e["suffix"]] = str(target)

    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "asset_id": asset_id,
                "resolution": resolution,
                "files": manifest_files,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "asset_id": asset_id,
        "resolution": resolution,
        "fbx_path": fbx_path,
        "texture_parts": texture_parts,
        "asset_page": f"https://polyhaven.com/a/{asset_id}",
        "source_url": f"{API_BASE}/files/{urllib.parse.quote(asset_id)}",
        "license": "CC0-1.0",
        "license_url": "https://polyhaven.com/license",
        "files_hash": manifest_files,
        "size_bytes": sum(f["size"] for f in manifest_files.values()),
        "source": "download" if downloaded_any else "cache",
        "cache_dir": str(dest_dir),
    }
