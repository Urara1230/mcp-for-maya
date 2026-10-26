"""Maya connection bootstrap guide.

Provides automated setup and fallback instructions for establishing
the Maya command port connection. Handles platform differences
(Windows/Linux/macOS) and Maya version detection.

Key responsibilities:
1. Managed marker-block install/uninstall of userSetup.py
2. Detect Maya installation paths per platform
3. Provide human-readable fallback instructions when auto-setup fails
4. Diagnose connection issues and suggest fixes

Marker-block semantics (only the marker region is rewritten, the rest
of userSetup.py is preserved):
- The block lives between "# >>> mcp-for-maya >>>" / "# <<< mcp-for-maya <<<".
- Missing file        -> create it directly.
- Existing file, no marker -> return the proposed block first; only a
  second call with confirm=True merges (appends) the block.
- Existing marker     -> replace in place (idempotent upgrade).
- Unparseable file    -> refuse, report fallback guidance.
- Every write first creates <file>.bak.<timestamp> and reports its path.
- uninstall strips only the marker region; when the file held nothing
  else, it asks before deleting (remove_empty_file=True).
"""

from __future__ import annotations

import datetime
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maya_mcp_server.utils import get_platform


logger = logging.getLogger(__name__)

# Default command port
DEFAULT_COMMAND_PORT = 7001

# Maya version years we scan for (detection range, not a support claim).
SUPPORTED_MAYA_VERSIONS = [2020, 2022, 2023, 2024, 2025, 2026]

MARKER_BEGIN = "# >>> mcp-for-maya >>>"
MARKER_END = "# <<< mcp-for-maya <<<"


def _marker_block(port: int) -> str:
    """The managed block written into userSetup.py.

    Self-protecting (try/except), idempotent (checks existing ports),
    no UI-ordering assumptions (evalDeferred to run post-init). The same
    text is shown in fallback instructions for manual installation.
    """
    return f'''\
{MARKER_BEGIN}
# Managed by mcp-for-maya — do not edit inside the markers.
def _mcp_for_maya_open_port():
    """Open the MCP command port (idempotent)."""
    import maya.cmds as cmds
    try:
        existing = cmds.commandPort(query=True, listPorts=True) or []
        if not any(str(p) == ":{port}" for p in existing):
            cmds.commandPort(name=":{port}", sourceType="python")
            print("[mcp-for-maya] command port :{port} open")
    except Exception as e:
        print("[mcp-for-maya] could not open command port :{port}: %s" % e)


try:
    import maya.cmds as _mcp_cmds
    _mcp_cmds.evalDeferred(
        "_mcp_for_maya_open_port()", lowestPriority=True
    )
except Exception:
    try:
        _mcp_for_maya_open_port()
    except Exception:
        pass
{MARKER_END}
'''


@dataclass
class MayaInstallInfo:
    """Detected Maya installation information."""

    version: str
    scripts_dir: Path
    user_setup_path: Path
    platform: str
    maya_app_dir: str | None
    is_valid: bool
    notes: list[str]


def _get_maya_app_dir() -> str | None:
    """Get MAYA_APP_DIR environment variable."""
    return os.environ.get("MAYA_APP_DIR")


def _platform_scan_roots() -> list[tuple[Path, str]]:
    """(root_dir, note) pairs to scan for <root>/<version>/scripts."""
    system = get_platform()
    roots: list[tuple[Path, str]] = []
    maya_app_dir = _get_maya_app_dir()
    if maya_app_dir:
        roots.append((Path(maya_app_dir), f"MAYA_APP_DIR={maya_app_dir}"))
    home = Path.home()
    if system == "windows":
        roots.append(
            (
                Path(os.environ.get("USERPROFILE", str(home))) / "Documents" / "maya",
                "Windows Documents/maya",
            )
        )
        roots.append(
            (
                Path(os.environ.get("LOCALAPPDATA", "")) / "Autodesk" / "Maya",
                "LOCALAPPDATA/Autodesk/Maya",
            )
        )
    elif system == "linux":
        roots.append((home / "maya", "~/maya"))
    elif system == "macos":
        roots.append(
            (
                home / "Library" / "Preferences" / "Autodesk" / "maya",
                "~/Library/Preferences/Autodesk/maya",
            )
        )
    return roots


def find_maya_installations() -> list[MayaInstallInfo]:
    """Find all Maya installations on the current platform.

    Single scanner driven by _platform_scan_roots — one loop for every
    platform and every root (deduplicated by scripts_dir).
    """
    system = get_platform()
    results: list[MayaInstallInfo] = []
    for root, note in _platform_scan_roots():
        if not str(root) or not root.exists():
            continue
        for ver in SUPPORTED_MAYA_VERSIONS:
            scripts_dir = root / str(ver) / "scripts"
            if scripts_dir.exists() and not any(r.scripts_dir == scripts_dir for r in results):
                results.append(
                    MayaInstallInfo(
                        version=str(ver),
                        scripts_dir=scripts_dir,
                        user_setup_path=scripts_dir / "userSetup.py",
                        platform=system,
                        maya_app_dir=str(root),
                        is_valid=True,
                        notes=[f"{note}: {scripts_dir}"],
                    )
                )
    return results


# ---------------------------------------------------------------------------
# Marker-block install/uninstall
# ---------------------------------------------------------------------------


def generate_user_setup_content(port: int = DEFAULT_COMMAND_PORT) -> str:
    """Full userSetup.py content for a fresh file — just the managed block.

    Byte-identical to what install writes and to the block shown in the
    manual fallback instructions.
    """
    return _marker_block(port)


def _backup_file(path: Path) -> str:
    """Copy path -> path.bak.<timestamp>; returns the backup path."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{ts}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return str(backup)


def _block_region(lines: list[str]) -> tuple[int, int] | None:
    """(start, end) line indexes of the marker region, or None.

    (start, -1) marks an unterminated block (BEGIN without END).
    """
    start = end = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == MARKER_BEGIN and start < 0:
            start = i
        elif stripped == MARKER_END and start >= 0:
            end = i
            break
    if start < 0:
        return None
    return (start, end)


def _strip_block(content: str) -> str | None:
    """Remove the marker region; None when absent/unterminated."""
    lines = content.splitlines(keepends=True)
    region = _block_region(lines)
    if region is None or region[1] < 0:
        return None
    start, end = region
    return "".join(lines[:start] + lines[end + 1 :])


def _replace_block(content: str, block: str) -> str | None:
    """Swap the marker region for a fresh block; None when absent."""
    lines = content.splitlines(keepends=True)
    region = _block_region(lines)
    if region is None or region[1] < 0:
        return None
    start, end = region
    return "".join(lines[:start]) + block + "".join(lines[end + 1 :])


def install_user_setup(
    port: int = DEFAULT_COMMAND_PORT,
    target_version: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Merge the managed marker block into userSetup.py.

    Args:
        port: Command port number.
        target_version: Specific Maya version to target (e.g., "2024").
        dry_run: If True, only report what would happen.
        confirm: Required to merge into an existing userSetup.py that
            does not yet contain the marker block. The first call on
            such a file returns action="needs_confirm" plus the
            proposed block; nothing is written.

    Returns:
        Dict with per-install results. Every write is preceded by a
        <file>.bak.<timestamp> backup whose path is reported.
    """
    installations = find_maya_installations()
    if not installations:
        return {
            "success": False,
            "error": {
                "code": "no_maya_found",
                "message": "No Maya installations found",
            },
            "diagnostics": get_connection_diagnostics(port),
        }

    if target_version:
        installations = [i for i in installations if i.version == target_version]
        if not installations:
            return {
                "success": False,
                "error": {
                    "code": "version_not_found",
                    "message": f"Maya version {target_version} not found",
                },
                "available_versions": [i.version for i in find_maya_installations()],
            }

    block = _marker_block(port)
    results: list[dict[str, Any]] = []

    for install in installations:
        target_path = install.user_setup_path
        entry: dict[str, Any] = {
            "version": install.version,
            "path": str(target_path),
            "action": "none",
        }

        exists = target_path.exists()
        content: str | None = None
        region: tuple[int, int] | None = None
        if exists:
            try:
                content = target_path.read_text(encoding="utf-8")
            except OSError as e:
                entry["action"] = "refused"
                entry["success"] = False
                entry["error"] = {
                    "code": "read_failed",
                    "message": f"Cannot read {target_path}: {e}",
                }
                results.append(entry)
                continue
            try:
                compile(content, str(target_path), "exec")
            except SyntaxError as e:
                entry["action"] = "refused"
                entry["success"] = False
                entry["error"] = {
                    "code": "unparseable_file",
                    "message": (
                        f"{target_path} is not parseable Python ({e}); refusing to modify it"
                    ),
                }
                entry["fallback"] = (
                    "Inspect the file manually, then paste the managed "
                    "block (see fallback instructions) or fix the syntax "
                    "error and retry."
                )
                results.append(entry)
                continue
            region = _block_region(content.splitlines(keepends=True))

        # ---------- dry run: report intent only ----------
        if dry_run:
            if not exists:
                entry["action"] = "would_create"
            elif region is None:
                entry["action"] = "would_merge"
            elif region[1] < 0:
                entry["action"] = "refused_unterminated_marker"
            else:
                entry["action"] = "would_update"
            results.append(entry)
            continue

        # ---------- write paths ----------
        if not exists:
            # 1) missing file -> create directly (block is the whole file)
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(block, encoding="utf-8")
            except OSError as e:
                entry["action"] = "failed"
                entry["success"] = False
                entry["error"] = {"code": "write_failed", "message": str(e)}
                results.append(entry)
                continue
            entry["action"] = "created"
            entry["success"] = True
            results.append(entry)
            continue

        assert content is not None

        if region is not None and region[1] < 0:
            # BEGIN marker without END -> refuse, don't guess
            entry["action"] = "refused"
            entry["success"] = False
            entry["error"] = {
                "code": "unterminated_marker",
                "message": (
                    f"{target_path} contains a '{MARKER_BEGIN}' marker "
                    f"without '{MARKER_END}'; refusing to modify"
                ),
            }
            entry["fallback"] = (
                "Remove the dangling marker line manually, or fix the file, then retry."
            )
            results.append(entry)
            continue

        if region is not None:
            # 3) existing marker -> replace in place (idempotent upgrade)
            new_content = _replace_block(content, block)
            assert new_content is not None
            if new_content == content:
                entry["action"] = "already_current"
                entry["success"] = True
                results.append(entry)
                continue
            try:
                entry["backup"] = _backup_file(target_path)
                target_path.write_text(new_content, encoding="utf-8")
            except OSError as e:
                entry["action"] = "failed"
                entry["success"] = False
                entry["error"] = {"code": "write_failed", "message": str(e)}
                results.append(entry)
                continue
            entry["action"] = "updated"
            entry["success"] = True
            results.append(entry)
            continue

        # 2) existing file without marker -> confirm-gated merge
        if not confirm:
            entry["action"] = "needs_confirm"
            entry["success"] = False
            entry["proposed_block"] = block
            entry["confirm_hint"] = (
                "userSetup.py already exists and has no mcp-for-maya "
                "marker block. Review proposed_block, then call install "
                "again with confirm=True to append it (the existing "
                "content is preserved and a backup is written first)."
            )
            results.append(entry)
            continue

        if content and not content.endswith("\n"):
            content += "\n"
        new_content = content + "\n" + block
        try:
            entry["backup"] = _backup_file(target_path)
            target_path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            entry["action"] = "failed"
            entry["success"] = False
            entry["error"] = {"code": "write_failed", "message": str(e)}
            results.append(entry)
            continue
        entry["action"] = "merged"
        entry["success"] = True
        results.append(entry)

    return {
        "success": any(r.get("success", False) for r in results),
        "results": results,
        "instructions": get_fallback_instructions(port),
    }


def uninstall_user_setup(
    target_version: str | None = None,
    remove_empty_file: bool = False,
) -> dict[str, Any]:
    """Strip the managed marker block from userSetup.py.

    Args:
        target_version: Specific Maya version; None targets all.
        remove_empty_file: When the file contains ONLY the marker block,
            delete it. Otherwise the emptied file is left in place and
            file_now_empty is reported.

    Returns:
        Dict with per-install results.
    """
    installations = find_maya_installations()
    if target_version:
        installations = [i for i in installations if i.version == target_version]

    results: list[dict[str, Any]] = []
    for install in installations:
        target_path = install.user_setup_path
        entry: dict[str, Any] = {
            "version": install.version,
            "path": str(target_path),
            "action": "not_installed",
        }
        if not target_path.exists():
            results.append(entry)
            continue

        try:
            content = target_path.read_text(encoding="utf-8")
        except OSError as e:
            entry["action"] = "failed"
            entry["success"] = False
            entry["error"] = {"code": "read_failed", "message": str(e)}
            results.append(entry)
            continue

        region = _block_region(content.splitlines(keepends=True))
        if region is None:
            entry["action"] = "no_block"
            entry["success"] = True
            results.append(entry)
            continue
        if region[1] < 0:
            entry["action"] = "failed"
            entry["success"] = False
            entry["error"] = {
                "code": "unterminated_marker",
                "message": (
                    f"'{MARKER_BEGIN}' without '{MARKER_END}' in "
                    f"{target_path}; remove the marker manually"
                ),
            }
            results.append(entry)
            continue

        stripped = _strip_block(content)
        assert stripped is not None
        try:
            entry["backup"] = _backup_file(target_path)
        except OSError as e:
            entry["action"] = "failed"
            entry["success"] = False
            entry["error"] = {"code": "backup_failed", "message": str(e)}
            results.append(entry)
            continue

        if not stripped.strip():
            # File held only the block — ask before deleting.
            if remove_empty_file:
                try:
                    target_path.unlink()
                except OSError as e:
                    entry["action"] = "failed"
                    entry["success"] = False
                    entry["error"] = {"code": "remove_failed", "message": str(e)}
                    results.append(entry)
                    continue
                entry["action"] = "file_removed"
                entry["success"] = True
            else:
                try:
                    target_path.write_text(stripped, encoding="utf-8")
                except OSError as e:
                    entry["action"] = "failed"
                    entry["success"] = False
                    entry["error"] = {"code": "write_failed", "message": str(e)}
                    results.append(entry)
                    continue
                entry["action"] = "block_removed"
                entry["file_now_empty"] = True
                entry["success"] = True
                entry["suggestion"] = (
                    "userSetup.py contained only the mcp-for-maya block. "
                    "Call uninstall again with remove_empty_file=True to "
                    "delete the file, or leave the empty file in place."
                )
            results.append(entry)
            continue

        try:
            target_path.write_text(stripped, encoding="utf-8")
        except OSError as e:
            entry["action"] = "failed"
            entry["success"] = False
            entry["error"] = {"code": "write_failed", "message": str(e)}
            results.append(entry)
            continue
        entry["action"] = "block_removed"
        entry["success"] = True
        results.append(entry)

    return {
        "success": any(r.get("success", False) for r in results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Fallback instructions + diagnostics
# ---------------------------------------------------------------------------


def get_fallback_instructions(port: int = DEFAULT_COMMAND_PORT) -> str:
    """Human-readable manual-setup instructions.

    Honest copy: this text accompanies an install attempt — it says what
    the tool does, and gives a manual path that pastes the byte-identical
    managed block. Nothing claims a file was already generated.
    """
    system = get_platform()
    block = _marker_block(port)

    instructions = f"""## Maya MCP 连接引导 / Maya MCP Connection Guide

### 方式一：自动安装 / Option A: Managed Install

调用 `maya_setup_guide(action="install")` 会把下面的受管标记块写入
Maya 的 userSetup.py：文件不存在时直接创建；已存在且无标记块时先返回
proposed_block，需 `confirm=True` 二次调用才合并；已存在标记块时原位更新。
每次写入前会生成 `<file>.bak.<timestamp>` 备份。

Calling maya_setup_guide(action="install") merges the managed marker
block below into userSetup.py: missing files are created; existing
files without the block require a second call with confirm=True; an
existing block is updated in place. A <file>.bak.<timestamp> backup
precedes every write.

### 方式二：手动粘贴标记块 / Option B: Paste the block manually

如果自动安装未执行或失败，把下面这段原样追加到 userSetup.py 末尾
（与工具写入的内容逐字节相同），然后重启 Maya：

```python
{block}```

### 方式三：当前会话立即开端口 / Option C: Open the port now

在 Maya Script Editor（Python 模式）执行：

```python
import maya.cmds as cmds
cmds.commandPort(name=":{port}", sourceType="python")
```

验证：

```python
print(cmds.commandPort(query=True, listPorts=True))
```

应显示包含 `":{port}"` 的列表。

### 常见问题排查 / Troubleshooting

1. **端口被占用**: 关闭其他 Maya 实例，或换一个端口号
2. **防火墙阻止**: 确保本地防火墙允许 Maya 监听 localhost:{port}
3. **userSetup.py 未执行**: 确认标记块在正确的 scripts 目录文件中（见下）

### 各平台 scripts 目录路径

"""

    if system == "windows":
        instructions += """**Windows:**
- %MAYA_APP_DIR%\\<version>\\scripts\\userSetup.py
- C:\\Users\\<username>\\Documents\\maya\\<version>\\scripts\\userSetup.py
- 自定义: 如果设置了 MAYA_APP_DIR 环境变量，使用该变量指向的路径

例如 Maya 2024:
- %MAYA_APP_DIR%\\2024\\scripts\\userSetup.py
- C:\\Users\\<username>\\Documents\\maya\\2024\\scripts\\userSetup.py
"""
    elif system == "linux":
        instructions += """**Linux:**
- ~/maya/<version>/scripts/userSetup.py

例如 Maya 2024:
- ~/maya/2024/scripts/userSetup.py

注意: Maya on Linux 需要通过 Autodesk 官方安装。
"""
    elif system == "macos":
        instructions += """**macOS:**
- ~/Library/Preferences/Autodesk/maya/<version>/scripts/userSetup.py

例如 Maya 2024:
- ~/Library/Preferences/Autodesk/maya/2024/scripts/userSetup.py
"""

    instructions += f"""
### 环境变量说明

- MAYA_APP_DIR: Maya 的用户配置根目录。如果设置了此变量，Maya 会在此目录下查找 scripts。
- 可在系统环境变量中设置，也可在 Maya 安装目录的 Maya.env 文件中设置。

### 连接端口配置

默认端口: {port}
如需更改端口，重新运行 `maya_setup_guide(action="install", port=<新端口>)`
（已存在的标记块会原位更新），并在 Script Editor 中用 Option C 立即生效。
"""

    return instructions


def get_connection_diagnostics(port: int = DEFAULT_COMMAND_PORT) -> dict[str, Any]:
    """Diagnose connection issues and provide actionable suggestions.

    Args:
        port: Command port to diagnose.

    Returns:
        Diagnostic results with issues and suggestions.
    """
    diagnostics: dict[str, Any] = {
        "platform": get_platform(),
        "port": port,
        "maya_running": False,
        "port_open": False,
        "user_setup_found": [],
        "issues": [],
        "suggestions": [],
    }

    # Check if Maya is running
    try:
        import psutil

        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info.get("name", "")
                if name and name.lower() in ("maya", "maya.exe"):
                    diagnostics["maya_running"] = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        diagnostics["issues"].append("psutil not available — cannot detect Maya process")

    # Check if port is listening
    try:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            result = s.connect_ex(("127.0.0.1", port))
            diagnostics["port_open"] = result == 0
    except OSError as e:
        diagnostics["issues"].append(f"Socket check failed: {e}")

    # Check for userSetup.py
    installations = find_maya_installations()
    for install in installations:
        if install.user_setup_path.exists():
            diagnostics["user_setup_found"].append(
                {
                    "version": install.version,
                    "path": str(install.user_setup_path),
                }
            )

    # Generate issues and suggestions
    if not diagnostics["maya_running"]:
        diagnostics["issues"].append("Maya 未运行 / Maya is not running")
        diagnostics["suggestions"].append("请先启动 Maya / Please start Maya first")

    if diagnostics["maya_running"] and not diagnostics["port_open"]:
        diagnostics["issues"].append(
            f"Maya 正在运行但端口 {port} 未打开 / Maya is running but port {port} is not open"
        )
        if not diagnostics["user_setup_found"]:
            diagnostics["suggestions"].append(
                "未找到 userSetup.py，请运行安装引导 / "
                "No userSetup.py found. Run the connection guide to install it."
            )
        else:
            diagnostics["suggestions"].append(
                "userSetup.py 已存在但端口未打开，可能需要重启 Maya / "
                "userSetup.py exists but port not open, Maya restart may be needed"
            )
            diagnostics["suggestions"].append(
                f"或者手动在 Script Editor 中执行: "
                f'cmds.commandPort(name=":{port}", sourceType="python")'
            )

    if diagnostics["port_open"]:
        diagnostics["suggestions"].append(
            f"端口 {port} 已打开，可以连接 / Port {port} is open, ready to connect"
        )

    return diagnostics


def get_agent_connection_instructions(port: int = DEFAULT_COMMAND_PORT) -> str:
    """Get instructions for the AI agent to guide the user through connection setup.

    This is the main entry point for agent-facing guidance. The agent should
    use these instructions when:
    1. list_sessions returns empty
    2. Connection to Maya fails
    3. User asks how to set up Maya MCP

    Args:
        port: Command port number.

    Returns:
        Structured instructions for the agent to relay to the user.
    """
    diagnostics = get_connection_diagnostics(port)
    system = diagnostics["platform"]

    agent_guide = f"""# Maya MCP 连接诊断 / Connection Diagnostics

## 当前状态

- 平台: {system}
- 目标端口: {port}
- Maya 运行: {"✅ 是" if diagnostics["maya_running"] else "❌ 否"}
- 端口监听: {"✅ 已打开" if diagnostics["port_open"] else "❌ 未打开"}
- userSetup.py: {"✅ 已找到" if diagnostics["user_setup_found"] else "❌ 未找到"}
"""

    if diagnostics["user_setup_found"]:
        for found in diagnostics["user_setup_found"]:
            agent_guide += f"  - Maya {found['version']}: {found['path']}\n"

    if diagnostics["issues"]:
        agent_guide += "\n## 发现的问题\n\n"
        for issue in diagnostics["issues"]:
            agent_guide += f"- {issue}\n"

    if diagnostics["suggestions"]:
        agent_guide += "\n## 建议操作\n\n"
        for i, suggestion in enumerate(diagnostics["suggestions"], 1):
            agent_guide += f"{i}. {suggestion}\n"

    # Add manual instructions when the port is not open
    if not diagnostics["port_open"]:
        agent_guide += f"""
## 手动开启端口指引 / Manual Port Opening Guide

请让用户在 Maya 中执行以下操作：

### 步骤 1：打开 Script Editor
在 Maya 菜单栏: Windows → General Editors → Script Editor

### 步骤 2：切换到 Python 模式
Script Editor 底部有语言选择器，确保显示为 **Python**（不是 MEL）

### 步骤 3：输入并执行代码
```python
import maya.cmds as cmds
cmds.commandPort(name=":{port}", sourceType="python")
```
选中代码后按 Ctrl+Enter 执行。

### 步骤 4：验证
执行后再次调用 list_sessions 检查连接。
"""

    # Managed-install guidance — always shown; it is guidance, not detection.
    agent_guide += """
## 自动安装 userSetup.py

可通过 MCP 工具自动把受管标记块合并进 userSetup.py：

maya_setup_guide(action="install")

- 文件不存在 → 直接创建（内容仅为标记块）
- 文件已存在且无标记块 → 先返回 proposed_block，confirm=True 二次调用才合并
- 已存在标记块 → 原位更新
- 卸载: maya_setup_guide(action="uninstall")（只摘标记块）
"""

    return agent_guide
