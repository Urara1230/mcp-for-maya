"""PyPI index dual-source probe — weekly drift-canary lane.

Catches the index-registration regression this lane was written for: the 0.4.0
upload landed (files + attestation + per-version JSON) but the simple index
rolled back during a PyPI maintenance window — pip resolution silently fell to
0.3.0.

Two sources, independent lanes:

- PRIMARY: the PEP 691 simple index — the install-resolution truth
  surface — in BOTH serializations (JSON and HTML; pip negotiates
  either). Missing in both = install face broken (highest severity).
  Missing in only one = serialization split (CDN-stale edge — pip may
  still resolve through the fresh side; still red, the inconsistency
  is real and needs human eyes, but it is NOT a dead install face).
- SECONDARY: the project JSON ``info.version`` must equal the expected
  version (the ``releases`` key is deprecated and not consulted).
  Simple green + JSON red = API drift, not install breakage.

Both green = healthy. Maintenance-window false reds are digested by
human attribution through the notify channel — the drift job goes
red and ``notify_drift_canary.py`` lands the dedup issue. This probe is
deliberately fail-loud: transport errors and non-200s are red too, not
silently skipped.

Env:
    PACKAGE     — package name (default "mcp-for-maya")
    LATEST_TAG  — expected version source: the latest git tag with the
                  "v" prefix stripped ("v0.4.0" -> "0.4.0"). REQUIRED —
                  the CI step resolves it via ``git ls-remote --tags``.
    TIMEOUT_S   — per-request timeout in seconds (default 30)

Exit: 0 both lanes green; 1 any lane red; 2 misuse (LATEST_TAG unset).

The probe doubles as the runnable answer to "would a silent schedule
failure also hide a PyPI regression": GitHub auto-disables schedules
after 60 days of repo inactivity, which the external Task Scheduler
carrier is immune to — this lane rides that same carrier.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable


PACKAGE = "mcp-for-maya"
SIMPLE_URL = "https://pypi.org/simple/{package}/"
JSON_URL = "https://pypi.org/pypi/{package}/json"
PEP691_ACCEPT = "application/vnd.pypi.simple.v1+json"
HTML_ACCEPT = "text/html"
TIMEOUT_S = 30

# (url, accept_header) -> (http_status, body); status 0 = transport error.
Fetcher = Callable[[str, str], tuple[int, str]]


def _urllib_fetch(url: str, accept: str, timeout: float = TIMEOUT_S) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.reason or ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)


def _versions_from_simple(body: str) -> set[str] | None:
    """PEP 691 versions list, or None when the payload is not usable."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    versions = data.get("versions") if isinstance(data, dict) else None
    if isinstance(versions, list):
        return {str(v) for v in versions}
    return None


def _versions_from_html(body: str, package: str) -> set[str] | None:
    """Versions parsed from HTML-index anchor filenames, or None.

    Filenames look like ``mcp_for_maya-0.4.0-py3-none-any.whl`` /
    ``mcp_for_maya-0.4.0.tar.gz``; the leading ``<name>-`` is stripped
    and the version runs to the first '-' of the dist-info tail.
    """
    if not body.lstrip().startswith("<"):
        return None
    name = "".join("[-_]" if c in "-_" else re.escape(c) for c in package)
    found = set()
    for m in re.finditer(rf"{name}-(\d[^-\"'>]*)-", body):
        found.add(m.group(1))
    for m in re.finditer(rf"{name}-(\d[^-\"'>]*)\.tar\.gz", body):
        found.add(m.group(1))
    return found if found else set()


def _version_key(version: str) -> tuple:
    """Numeric-aware sort key — '0.10.0' sorts after '0.4.0'."""
    return tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.+-]", version))


def _latest(versions: set[str]) -> str:
    return max(versions, key=_version_key) if versions else "(none)"


def _info_version(body: str) -> str | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    info = data.get("info") if isinstance(data, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return str(version) if version is not None else None


def probe(package: str, expected: str, fetch: Fetcher) -> list[str]:
    """Red-lane descriptions; empty list = all sources healthy.

    The simple index is checked in both serializations — pip negotiates
    PEP 691 JSON first but falls back to HTML, so "install face broken"
    is only claimed when NEITHER lists the version (the CDN-split
    observation: JSON edge stale while HTML serves fresh).
    """
    problems: list[str] = []
    simple = SIMPLE_URL.format(package=package)

    sj, tj = fetch(simple, PEP691_ACCEPT)
    sh, th = fetch(simple, HTML_ACCEPT)
    j_ver = _versions_from_simple(tj) if sj == 200 else None
    h_ver = _versions_from_html(th, package) if sh == 200 else None
    if j_ver is None:
        problems.append(
            f"simple index JSON unusable (HTTP {sj or 'transport-error'}) — "
            "cannot confirm install face"
        )
    if h_ver is None:
        problems.append(
            f"simple index HTML unusable (HTTP {sh or 'transport-error'}) — "
            "cannot confirm install face"
        )
    if j_ver is not None and h_ver is not None:
        if expected not in j_ver and expected not in h_ver:
            problems.append(
                f"simple index missing {expected} in BOTH serializations "
                f"(latest listed: {_latest(j_ver | h_ver)}) — install face "
                "broken (highest severity): pip resolves an older release "
                "(D-139 class)"
            )
        elif expected not in j_ver:
            problems.append(
                f"PEP 691 JSON serialization stale: missing {expected} "
                f"(latest listed: {_latest(j_ver)}) while HTML lists it — "
                "CDN split; pip still resolves via HTML but the JSON edge "
                "is inconsistent (D-139 residue)"
            )
        elif expected not in h_ver:
            problems.append(
                f"HTML serialization stale: missing {expected} while JSON "
                "lists it — CDN split (install resolves via JSON; the HTML "
                "edge is inconsistent)"
            )

    status, text = fetch(JSON_URL.format(package=package), "application/json")
    if status != 200:
        problems.append(
            f"project JSON HTTP {status or 'transport-error'} — JSON API unreachable (API drift)"
        )
    else:
        info_version = _info_version(text)
        if info_version is None:
            problems.append("project JSON unparseable — JSON API drift")
        elif info_version != expected:
            problems.append(
                f"project JSON info.version={info_version} != {expected} — "
                "JSON API drift (secondary lane; install face unaffected "
                "when the simple index is green)"
            )
    return problems


def main() -> int:
    package = os.environ.get("PACKAGE", PACKAGE)
    tag = os.environ.get("LATEST_TAG", "").strip()
    if not tag:
        print(
            "::error::check_pypi_index: LATEST_TAG unset — expected the "
            "latest git tag (e.g. v0.4.0) resolved by the CI step"
        )
        return 2
    expected = tag.lstrip("v")
    try:
        timeout = float(os.environ.get("TIMEOUT_S", str(TIMEOUT_S)))
    except ValueError:
        print("::error::check_pypi_index: TIMEOUT_S not a number")
        return 2

    def fetch(url: str, accept: str) -> tuple[int, str]:
        return _urllib_fetch(url, accept, timeout)

    problems = probe(package, expected, fetch)
    for msg in problems:
        print(f"::error::pypi-index[{package}]: {msg}")
    if problems:
        print(
            "Probe red — the D-123 notify channel turns this into a dedup "
            "issue; maintenance-window false reds get human attribution."
        )
        return 1
    print(f"pypi-index[{package}]: simple + JSON both carry {expected} — OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
