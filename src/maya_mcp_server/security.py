"""Security pipeline primitives for maya-mcp-server.

All 22 MCP tools funnel through the SecurityPipeline middleware
(pipeline.py): input validation -> rate limit -> pattern scan ->
dispatch -> audit. This module is a safety net for confused-deputy
scenarios, NOT a security boundary (see SECURITY.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

# Maximum code size (1MB) to prevent memory exhaustion
MAX_CODE_SIZE = 1_048_576

# Maximum module name length
MAX_MODULE_NAME_LENGTH = 256

# Module name validation pattern (Python identifier with dots)
MODULE_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$")

# Rate limiting configuration (split read/write token buckets)
RATE_LIMIT_WINDOW = 60.0  # seconds
RATE_LIMIT_READ_MAX_CALLS = 100  # read-class tools per window per session
RATE_LIMIT_WRITE_MAX_CALLS = 20  # mutation-class tools per window per session

# Audit log configuration
AUDIT_APP_NAME = "mcp-for-maya"
AUDIT_LOG_FILENAME = "audit.jsonl"
AUDIT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB per segment
AUDIT_BACKUP_COUNT = 5
AUDIT_SUMMARY_MAX_CHARS = 200
AUDIT_VALUE_PREVIEW_CHARS = 60


# ---------------------------------------------------------------------------
# Error contract: host-side failures raise coded exceptions which
# surface through MCP isError with a [code] prefix embedded in the text.
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """Host-side pipeline failure.

    The rendered message always carries a [code] prefix so the agent can
    classify the failure from the isError text alone.
    """

    code = "pipeline_error"

    def __init__(self, message: str = "", *, suggestion: str | None = None) -> None:
        self.message = message
        self.suggestion = suggestion
        text = f"[{self.code}] {message}"
        if suggestion:
            text += f" (suggestion: {suggestion})"
        super().__init__(text)


class InputValidationError(PipelineError):
    """Raised when input validation fails."""

    code = "invalid_input"


class RateLimitExceededError(PipelineError):
    """Raised when a session exceeds its rate limit bucket."""

    code = "rate_limited"


class PatternBlockedError(PipelineError):
    """Raised when a parameter hits a zero-false-positive block rule."""

    code = "blocked_pattern"


class SessionLookupError(PipelineError):
    """Raised when a call needs a Maya session but none can be resolved."""

    code = "session_unavailable"


class ServerNotReadyError(PipelineError):
    """Raised when a host-side service is used before initialization."""

    code = "server_not_started"


class GuiSessionRequiredError(PipelineError):
    """Raised when a GUI-only tool is called on a headless session.

    Host-side gate of the double headless check: the
    framed_channel short-circuit raises this before any round trip.
    The Maya-side layer returns the same code as a domain error.
    """

    code = "gui_session_required"


class PolicyDisabledError(PipelineError):
    """Raised when an operator policy flag disables a tool.

    Misuse guardrail, NOT a hostile-agent boundary: an agent that can run
    execute_code bypasses every flag here. The threat model restates that
    (docs/threat-model.md); this class only makes the operator's intent
    mechanical and auditable.
    """

    code = "policy_disabled"


class CaptureEmptyError(PipelineError):
    """Raised when a visual capture produces a zero-byte artifact.

    Hard server-side check: headless playblast empirically
    writes empty files without erroring - never pass that through as
    success.
    """

    code = "capture_empty"


class InvalidCaptureError(PipelineError):
    """Raised when a capture payload fails integrity checks."""

    code = "capture_invalid"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def validate_code_size(code: str, max_size: int = MAX_CODE_SIZE) -> None:
    """Validate that code does not exceed maximum size.

    Args:
        code: Python code string to validate
        max_size: Maximum allowed size in bytes

    Raises:
        InputValidationError: If code exceeds max size
    """
    size = len(code.encode("utf-8"))
    if size > max_size:
        raise InputValidationError(
            f"Code size ({size:,} bytes) exceeds maximum allowed ({max_size:,} bytes)"
        )


def validate_module_name(name: str) -> None:
    """Validate module name format and length.

    Args:
        name: Module name to validate

    Raises:
        InputValidationError: If name is invalid
    """
    if not name:
        raise InputValidationError("Module name cannot be empty")

    if len(name) > MAX_MODULE_NAME_LENGTH:
        raise InputValidationError(
            f"Module name length ({len(name)}) exceeds maximum ({MAX_MODULE_NAME_LENGTH})"
        )

    if not MODULE_NAME_PATTERN.match(name):
        raise InputValidationError(
            f"Invalid module name '{name}': must be a valid Python dotted identifier "
            f"(e.g., 'mypackage.mymodule')"
        )


def validate_session_key(session_key: str | None) -> str | None:
    """Validate session key format.

    Args:
        session_key: Session key to validate (host:port format)

    Returns:
        Validated session key or None

    Raises:
        InputValidationError: If format is invalid
    """
    if session_key is None:
        return None

    if not isinstance(session_key, str):
        raise InputValidationError("Session key must be a string")

    # Validate format: host:port
    parts = session_key.rsplit(":", 1)
    if len(parts) != 2:
        raise InputValidationError(
            f"Invalid session key format '{session_key}': expected 'host:port'"
        )

    host, port_str = parts
    try:
        port = int(port_str)
        if not (0 < port < 65536):
            raise ValueError
    except ValueError:
        raise InputValidationError(f"Invalid port in session key '{session_key}': must be 1-65535")

    return session_key


# ---------------------------------------------------------------------------
# Rate limiting: real token buckets, split read/write, per session
# ---------------------------------------------------------------------------


@dataclass
class TokenBucket:
    """A real token bucket: capacity + continuous refill.

    Starts full; each take() consumes one token. Refill accrues
    refill_per_sec tokens up to capacity.
    """

    capacity: float
    refill_per_sec: float
    tokens: float = field(default=0.0)
    updated: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)

    def refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.refill_per_sec)
        self.updated = now

    def take(self, n: float = 1.0) -> bool:
        """Consume n tokens if available."""
        self.refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def retry_after(self) -> float:
        """Seconds until one more token is available."""
        self.refill()
        if self.tokens >= 1.0:
            return 0.0
        if self.refill_per_sec <= 0:
            return float("inf")
        return (1.0 - self.tokens) / self.refill_per_sec


@dataclass
class RateLimiter:
    """Per-session token bucket rate limiter."""

    window: float = RATE_LIMIT_WINDOW
    max_calls: int = RATE_LIMIT_READ_MAX_CALLS
    _buckets: dict[str, TokenBucket] = field(default_factory=dict)

    def _bucket(self, session_key: str) -> TokenBucket:
        bucket = self._buckets.get(session_key)
        if bucket is None:
            bucket = TokenBucket(
                capacity=float(self.max_calls),
                refill_per_sec=self.max_calls / self.window if self.window > 0 else 0.0,
            )
            self._buckets[session_key] = bucket
        return bucket

    def try_consume(self, session_key: str) -> bool:
        """Consume one token for the session.

        Returns:
            True if a token was consumed, False if the bucket is empty
        """
        allowed = self._bucket(session_key).take()
        if not allowed:
            logger.warning(
                f"Rate limit exceeded for session {session_key}: "
                f"{self.max_calls} calls per {self.window}s"
            )
        return allowed

    def retry_after(self, session_key: str) -> float:
        """Seconds until the session may call again."""
        return self._bucket(session_key).retry_after()

    def get_remaining(self, session_key: str) -> int:
        """Remaining calls available right now."""
        bucket = self._bucket(session_key)
        bucket.refill()
        return max(0, int(bucket.tokens))


# ---------------------------------------------------------------------------
# Pattern scanning: warn-by-default, block only precise rules,
# detection decoupled from enforcement via a rule_id x tool x param
# exclusion table.
# ---------------------------------------------------------------------------

# Param-name -> kind classification used by rules and validation.
PARAM_KINDS: dict[str, str] = {
    "code": "code",
    "filename": "filename",
    "filepath": "filename",
    "path": "filename",
}


@dataclass(frozen=True)
class PatternRule:
    """One pattern-scan rule.

    param_kinds=None applies to every string parameter; otherwise the rule
    only fires on params whose classified kind is in the set.
    """

    rule_id: str
    regex: re.Pattern[str]
    message: str
    action: str = "warn"  # "warn" | "block"
    param_kinds: frozenset[str] | None = None


# Broad warn-only rules: recorded to the audit log, never block.
PATTERN_RULES: list[PatternRule] = [
    # --- precise block rules (zero-false-positive class) ---
    PatternRule(
        "code-os-system",
        re.compile(r"\bos\.system\s*\(|\bos\.popen\s*\("),
        "os command execution",
        action="block",
        param_kinds=frozenset({"code"}),
    ),
    PatternRule(
        "code-subprocess",
        re.compile(r"\bsubprocess\b"),
        "subprocess usage",
        action="block",
        param_kinds=frozenset({"code"}),
    ),
    PatternRule(
        "code-eval",
        re.compile(r"\beval\s*\("),
        "eval() usage",
        action="block",
        param_kinds=frozenset({"code"}),
    ),
    PatternRule(
        "file-traversal",
        re.compile(r"\.\.[\\/]"),
        "path traversal sequence",
        action="block",
        param_kinds=frozenset({"filename"}),
    ),
    # --- broad warn-only rules (detection, not enforcement) ---
    PatternRule(
        "warn-subprocess-import",
        re.compile(r"__import__\s*\(\s*['\"]subprocess['\"]"),
        "subprocess import detected",
        param_kinds=None,
    ),
    PatternRule(
        "warn-os-import",
        re.compile(r"__import__\s*\(\s*['\"]os['\"]"),
        "os module import via __import__",
        param_kinds=None,
    ),
    PatternRule(
        "warn-os-exec",
        re.compile(r"os\.(system|popen|exec|spawn)"),
        "os command execution",
        param_kinds=None,
    ),
    PatternRule(
        "warn-subprocess-call",
        re.compile(r"subprocess\.(run|call|Popen|check_output|check_call)"),
        "subprocess execution",
        param_kinds=None,
    ),
    PatternRule(
        "warn-eval",
        re.compile(r"eval\s*\("),
        "eval() usage",
        param_kinds=None,
    ),
    PatternRule(
        "warn-exec",
        re.compile(r"exec\s*\("),
        "exec() usage",
        param_kinds=None,
    ),
    PatternRule(
        "warn-builtins",
        re.compile(r"__builtins__"),
        "builtins access",
        param_kinds=None,
    ),
    PatternRule(
        "warn-file-write",
        re.compile(r"open\s*\(.+['\"]w['\"]"),
        "file write operation",
        param_kinds=None,
    ),
    PatternRule(
        "warn-shutil",
        re.compile(r"shutil\.(rmtree|move|copy)"),
        "filesystem operations",
        param_kinds=None,
    ),
]

# Tuning table: (rule_id, tool_name, param_name) triples that suppress a hit.
# Kept deliberately small \u2014 every entry is a documented false-positive escape.
PATTERN_EXCLUSIONS: set[tuple[str, str, str]] = set()


def scan_tool_params(
    tool_name: str,
    params: dict[str, Any],
    exclusions: set[tuple[str, str, str]] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Scan all string params of a tool call against PATTERN_RULES.

    Args:
        tool_name: MCP tool name (for exclusion matching).
        params: Tool call arguments.
        exclusions: Optional extra (rule_id, tool, param) suppression set.

    Returns:
        (warnings, blocked): warnings are audit-only hits; blocked are
        hits on block-action rules.
    """
    excl = PATTERN_EXCLUSIONS | (exclusions or set())
    warnings: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []
    for name, value in params.items():
        if not isinstance(value, str):
            continue
        kind = PARAM_KINDS.get(name, "string")
        for rule in PATTERN_RULES:
            if rule.param_kinds is not None and kind not in rule.param_kinds:
                continue
            if (rule.rule_id, tool_name, name) in excl:
                continue
            m = rule.regex.search(value)
            if not m:
                continue
            hit = {
                "rule_id": rule.rule_id,
                "param": name,
                "snippet": m.group(0)[:80],
                "message": rule.message,
            }
            (blocked if rule.action == "block" else warnings).append(hit)
    return warnings, blocked


# Backwards-compatible helper kept for ad-hoc scans and tests.
def scan_for_dangerous_patterns(code: str) -> list[str]:
    """Scan code for potentially dangerous patterns (warn-only list).

    This does NOT block execution - it returns warning messages.
    """
    warnings, _blocked = scan_tool_params("_adhoc", {"code": code})
    return [w["message"] for w in warnings]


def compute_code_hash(code: str) -> str:
    """Compute SHA-256 hash of code for audit logging."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Summaries + sanitization
# ---------------------------------------------------------------------------

# Param names that must never be recorded (credential-shaped).
_SENSITIVE_PARAM_RE = re.compile(r"token|secret|password|credential|api[_-]?key", re.I)


def summarize_params(params: dict[str, Any]) -> str:
    """Build a sanitized <=200-char input summary for the audit log.

    Records param names with short previews \u2014 never a bare hash of the
    whole input, never credential-shaped values. Large strings get a
    head preview + length + short sha256 for dedup.
    """
    parts: list[str] = []
    for name, value in params.items():
        if _SENSITIVE_PARAM_RE.search(name):
            parts.append(f"{name}=<redacted>")
            continue
        if isinstance(value, str):
            if len(value) <= AUDIT_VALUE_PREVIEW_CHARS:
                preview = value.replace("\n", "\\n")
                parts.append(f'{name}="{preview}"')
            else:
                head = value[:40].replace("\n", "\\n")
                digest = compute_code_hash(value)[:8]
                parts.append(f'{name}="{head}..."({len(value)}B sha:{digest})')
        elif isinstance(value, (int, float, bool)) or value is None:
            parts.append(f"{name}={value!r}")
        else:
            parts.append(f"{name}=<{type(value).__name__}>")
    summary = " ".join(parts)
    if len(summary) > AUDIT_SUMMARY_MAX_CHARS:
        summary = summary[: AUDIT_SUMMARY_MAX_CHARS - 3] + "..."
    return summary


def sanitize_error_message(message: str) -> str:
    """Sanitize error messages to avoid leaking internal paths."""
    # Windows absolute paths (drive letter + separators + filename)
    sanitized = re.sub(r"[A-Za-z]:[\\/][^\s\"']+", "<path>", message)
    # Unix home paths
    sanitized = re.sub(r"/home/[^/]+/", "/.../", sanitized)
    sanitized = re.sub(r"/Users/[^/]+/", "/.../", sanitized)
    return sanitized


# ---------------------------------------------------------------------------
# Audit log: dedicated JSONL file, independent rotation, 0600,
# dual-written to the app logger, and never blocks tool execution.
# ---------------------------------------------------------------------------


def default_audit_log_path() -> Path:
    """Audit log path under the platformdirs user log directory."""
    import platformdirs

    return Path(platformdirs.user_log_dir(AUDIT_APP_NAME)) / AUDIT_LOG_FILENAME


class AuditLogger:
    """Append-only JSONL audit writer with size-based rotation.

    Rotates audit.jsonl -> audit.jsonl.N (N up to backup_count). Write
    failures are logged to the app logger and swallowed \u2014 auditing is an
    observability surface, never a tool-execution blocker.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_bytes: int = AUDIT_MAX_BYTES,
        backup_count: int = AUDIT_BACKUP_COUNT,
    ) -> None:
        self.path = path if path is not None else default_audit_log_path()
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._warned = False

    def record(self, event: dict[str, Any]) -> None:
        """Write one audit event; never raises.

        The event is always dual-written to the application logger, even
        when the JSONL write fails. A failed JSONL write warns once per
        failure streak (reset on the next success) rather than latching
        the whole sink off permanently.
        """
        try:
            line = json.dumps(event, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"audit event serialization failed: {e}")
            return
        try:
            self._append(line)
            self._warned = False
        except Exception as e:
            if not self._warned:
                logger.warning(f"audit log write failed ({self.path}): {e}")
                self._warned = True
        # Dual-write to the application logger (independent of the JSONL file)
        logger.info("audit %s", line)

    def _append(self, line: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size + len(line) + 1 > self.max_bytes:
            self._rotate()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            f = os.fdopen(fd, "a", encoding="utf-8")
        except Exception:
            os.close(fd)
            raise
        with f:
            f.write(line + "\n")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass  # best-effort on platforms without POSIX modes

    def _rotate(self) -> None:
        for i in range(self.backup_count - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i}")
            dst = self.path.with_name(f"{self.path.name}.{i + 1}")
            if src.exists():
                os.replace(src, dst)
        first = self.path.with_name(f"{self.path.name}.1")
        os.replace(self.path, first)


def build_audit_event(
    *,
    session_id: str,
    tool_name: str,
    params: dict[str, Any],
    outcome: str,
    duration_ms: float,
    warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build one audit event dict (the JSONL schema)."""
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "session_id": session_id,
        "tool_name": tool_name,
        "input_summary": summarize_params(params),
        "outcome": outcome,
        "duration_ms": round(duration_ms, 2),
        "warnings": warnings or [],
    }


@dataclass
class SecurityConfig:
    """Security configuration for the MCP server."""

    max_code_size: int = MAX_CODE_SIZE
    max_module_name_length: int = MAX_MODULE_NAME_LENGTH
    enable_dangerous_pattern_warning: bool = True
    block_dangerous_patterns: bool = True
    allow_remote_connections: bool = False
    rate_limit_enabled: bool = True
    rate_limit_window: float = RATE_LIMIT_WINDOW
    rate_limit_read_max_calls: int = RATE_LIMIT_READ_MAX_CALLS
    rate_limit_write_max_calls: int = RATE_LIMIT_WRITE_MAX_CALLS
    audit_enabled: bool = True
    audit_log_path: str | None = None
    pattern_exclusions: frozenset[tuple[str, str, str]] = frozenset()
