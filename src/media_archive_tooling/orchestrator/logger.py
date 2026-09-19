"""Persistent, append-only unified logger with secret redaction and JSONL format."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, Optional, Union
import uuid

# Secret patterns to redact
SECRET_PATTERNS = [
    re.compile(r"(Token\s+)([0-9a-zA-Z_\-]+)", re.IGNORECASE),
    re.compile(r"(Bearer\s+)([0-9a-zA-Z_\-]+)", re.IGNORECASE),
    re.compile(r"(api[_\-]?key[\"']?\s*[:=]\s*[\"']?)([0-9a-zA-Z_\-]+)", re.IGNORECASE),
    re.compile(r"(token[\"']?\s*[:=]\s*[\"']?)([0-9a-zA-Z_\-]+)", re.IGNORECASE),
    re.compile(r"(password[\"']?\s*[:=]\s*[\"']?)([0-9a-zA-Z_\-]+)", re.IGNORECASE),
    re.compile(r"(secret[\"']?\s*[:=]\s*[\"']?)([0-9a-zA-Z_\-]+)", re.IGNORECASE),
    re.compile(r"(authorization[\"']?\s*[:=]\s*[\"']?)([0-9a-zA-Z_\-]+)", re.IGNORECASE),
]


def redact_secrets(obj: Any) -> Any:
    """Recursively redact sensitive tokens, passwords, and keys."""
    if isinstance(obj, str):
        val = obj
        for pat in SECRET_PATTERNS:
            val = pat.sub(r"\1[REDACTED]", val)
        return val
    elif isinstance(obj, dict):
        clean = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("token", "secret", "password", "key", "auth")):
                clean[k] = "[REDACTED]"
            else:
                clean[k] = redact_secrets(v)
        return clean
    elif isinstance(obj, (list, tuple)):
        return [redact_secrets(item) for item in obj]
    return obj


class UnifiedArchiveLogger:
    """Persistent append-only logger writing structured JSONL entries to one canonical log file."""

    def __init__(
        self,
        log_path: Optional[Union[str, Path]] = None,
        run_id: Optional[str] = None,
        workflow: str = "all",
    ):
        if log_path is None:
            self.log_path = Path(".renamer/media-archive-tooling.log").resolve()
        else:
            self.log_path = Path(log_path).expanduser().resolve()

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if run_id is None:
            now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            self.run_id = f"run_{now_str}_{uuid.uuid4().hex[:8]}"
        else:
            self.run_id = run_id

        self.workflow = workflow

    def log(
        self,
        event: str,
        severity: str = "INFO",
        tool: str = "orchestrator",
        file_path: Optional[Union[str, Path]] = None,
        tracking_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append a structured, sanitized event record to the log file."""
        now_iso = datetime.now(timezone.utc).isoformat()
        entry = {
            "timestamp": now_iso,
            "run_id": self.run_id,
            "severity": severity.upper(),
            "workflow": self.workflow,
            "tool": tool,
            "event": event,
            "file_path": str(file_path) if file_path else None,
            "tracking_id": tracking_id,
            "details": redact_secrets(details or {}),
        }

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self.log_path, mode="a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

    def info(self, event: str, tool: str = "orchestrator", **kwargs: Any) -> None:
        self.log(event, severity="INFO", tool=tool, details=kwargs.get("details"), **{k: v for k, v in kwargs.items() if k != "details"})

    def warning(self, event: str, tool: str = "orchestrator", **kwargs: Any) -> None:
        self.log(event, severity="WARNING", tool=tool, details=kwargs.get("details"), **{k: v for k, v in kwargs.items() if k != "details"})

    def error(self, event: str, tool: str = "orchestrator", **kwargs: Any) -> None:
        self.log(event, severity="ERROR", tool=tool, details=kwargs.get("details"), **{k: v for k, v in kwargs.items() if k != "details"})
