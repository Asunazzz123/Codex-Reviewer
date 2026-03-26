#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any, Callable, Dict, List, Optional, Tuple


SERVER_NAME = "codex-reviewer-wrapper"
SERVER_VERSION = "0.4.0"
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_REVIEW_ARTIFACT = ".codex/review-report.md"
DEFAULT_REVIEW_MODE = "mcp"
DEFAULT_CLEANUP_SCOPE = "reviewer"
DEFAULT_PROBE_TIMEOUT_SECONDS = 10
LOG_PATH_ENV_VAR = "CODEX_REVIEWER_LOG_PATH"
TASK_MARKER_PATTERN = re.compile(r"^\[TASK_MARKER:\s*([^\]]+)\]\s*$")
REVIEWER_STALE_SECONDS = 15 * 60
DIAGNOSTIC_CHILD_SECONDS = 30
TRANSPORT_JSONL = "jsonl"
TRANSPORT_CONTENT_LENGTH = "content-length"
JOB_DIRNAME = "reviewer-jobs"
JOB_HEARTBEAT_SECONDS = 5
JOB_QUEUE_STALE_SECONDS = 30
OUTPUT_TAIL_LIMIT = 12000
ACTIVE_JOB_STATUSES = {"queued", "running"}
HOST_COMMAND_MARKERS = (
    "codex app-server",
    "Codex.app/Contents/Resources/codex app-server",
)
DIAGNOSTIC_PARENT_MARKERS = (
    "codex_reviewer_mcp.py",
    "subprocess.Popen([",
    "import json, os, subprocess, sys",
)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    command: str
    state: str = ""
    elapsed_seconds: Optional[int] = None
    started_at: Optional[float] = None


class McpStdioTransport:
    def __init__(self, reader: Any, writer: Any) -> None:
        self._reader = reader
        self._writer = writer
        self.mode: Optional[str] = None

    def read_message(self, *, allow_eof: bool) -> Optional[Dict[str, Any]]:
        if self.mode == TRANSPORT_JSONL:
            return _read_jsonl_message(self._reader, allow_eof=allow_eof)
        if self.mode == TRANSPORT_CONTENT_LENGTH:
            return _read_content_length_message(self._reader, allow_eof=allow_eof)
        return self._read_auto_message(allow_eof=allow_eof)

    def write_message(self, payload: Dict[str, Any]) -> None:
        if self.mode == TRANSPORT_CONTENT_LENGTH:
            _write_content_length_message(self._writer, payload)
            return
        _write_jsonl_message(self._writer, payload)

    def _read_auto_message(self, *, allow_eof: bool) -> Optional[Dict[str, Any]]:
        while True:
            line = self._reader.readline()
            if not line:
                if allow_eof:
                    return None
                raise EOFError("MCP stream closed before a message was received")
            if line in (b"\r\n", b"\n"):
                continue
            if line.lower().startswith(b"content-length:"):
                self.mode = TRANSPORT_CONTENT_LENGTH
                _diagnostic_log("transport_detected", mode=self.mode)
                return _read_content_length_message(self._reader, allow_eof=False, first_line=line)
            self.mode = TRANSPORT_JSONL
            _diagnostic_log("transport_detected", mode=self.mode)
            return _decode_json_message(line, source=self.mode)


def _home_path(*parts: str) -> Path:
    return Path.home().joinpath(*parts)


def _framework_root() -> Path:
    env_root = os.environ.get("CODEX_REVIEWER_FRAMEWORK_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _codex_binary() -> str:
    return os.environ.get("CODEX_BINARY", str(_home_path(".codex", "bin", "codex-latest")))


def _codex_config_path() -> Path:
    return _home_path(".codex", "config.toml")


def _now_shanghai() -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 8 * 3600))


def _format_epoch(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def _binary_exists(value: str) -> bool:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.exists()
    return shutil.which(value) is not None


def _diagnostic_log(event: str, **fields: Any) -> None:
    raw_path = os.environ.get(LOG_PATH_ENV_VAR)
    if not raw_path:
        return

    payload = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "event": event,
    }
    for key, value in fields.items():
        if value is not None:
            payload[key] = value

    try:
        log_path = Path(raw_path).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
            handle.write("\n")
    except OSError:
        return


def _decode_json_message(raw_message: bytes, *, source: str) -> Dict[str, Any]:
    try:
        decoded = raw_message.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"MCP {source} message is not valid UTF-8") from exc
    if not decoded:
        raise ValueError(f"MCP {source} message is empty")
    try:
        return json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP {source} message is not valid JSON") from exc


def _read_jsonl_message(stream: Any, *, allow_eof: bool) -> Optional[Dict[str, Any]]:
    while True:
        line = stream.readline()
        if not line:
            if allow_eof:
                return None
            raise EOFError("MCP JSONL stream closed before a message was received")
        if line in (b"\r\n", b"\n"):
            continue
        return _decode_json_message(line, source=TRANSPORT_JSONL)


def _read_content_length_message(
    stream: Any,
    *,
    allow_eof: bool,
    first_line: Optional[bytes] = None,
) -> Optional[Dict[str, Any]]:
    headers: Dict[str, str] = {}
    line = first_line
    while True:
        if line is None:
            line = stream.readline()
        if not line:
            if allow_eof and not headers:
                return None
            raise EOFError("MCP stream closed before a complete Content-Length frame was received")
        if line in (b"\r\n", b"\n"):
            break
        try:
            name, value = line.decode("utf-8").split(":", 1)
        except ValueError as exc:
            raise ValueError("MCP Content-Length frame has an invalid header line") from exc
        headers[name.lower()] = value.strip()
        line = None

    try:
        content_length = int(headers.get("content-length", "0"))
    except ValueError as exc:
        raise ValueError("MCP frame is missing a valid Content-Length header") from exc
    if content_length <= 0:
        raise ValueError("MCP frame is missing a valid Content-Length header")
    body = stream.read(content_length)
    if len(body) != content_length:
        raise EOFError("MCP stream ended before the full frame body was received")
    return _decode_json_message(body, source=TRANSPORT_CONTENT_LENGTH)


def _write_jsonl_message(stream: Any, payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stream.write(raw + b"\n")
    stream.flush()


def _write_content_length_message(stream: Any, payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8"))
    stream.write(raw)
    stream.flush()


_STDIO_TRANSPORT = McpStdioTransport(sys.stdin.buffer, sys.stdout.buffer)


def _read_message() -> Optional[Dict[str, Any]]:
    return _STDIO_TRANSPORT.read_message(allow_eof=True)


def _write_message(payload: Dict[str, Any]) -> None:
    _STDIO_TRANSPORT.write_message(payload)

def _json_rpc_result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _json_rpc_error(message_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def _tool_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _server_capabilities() -> dict[str, Any]:
    return {"tools": {"listChanged": False}}


def _extract_task_marker(prompt: str) -> Tuple[Optional[str], str]:
    lines = prompt.splitlines()
    if not lines:
        return None, prompt
    match = TASK_MARKER_PATTERN.match(lines[0].strip())
    if not match:
        return None, prompt
    remaining = "\n".join(lines[1:]).strip()
    return match.group(1).strip(), remaining


def _normalize_task_marker(task_marker: Optional[str]) -> Optional[str]:
    if task_marker is None:
        return None
    stripped = str(task_marker).strip()
    if not stripped:
        return None
    match = TASK_MARKER_PATTERN.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _render_task_marker(task_marker: Optional[str]) -> Optional[str]:
    normalized = _normalize_task_marker(task_marker)
    if not normalized:
        return None
    return f"[TASK_MARKER: {normalized}]"


def _now_epoch() -> float:
    return time.time()


def _artifact_root(cwd: Path) -> Path:
    return cwd / ".codex"


def _jobs_root(cwd: Path) -> Path:
    return _artifact_root(cwd) / JOB_DIRNAME


def _job_file(cwd: Path, job_id: str) -> Path:
    return _jobs_root(cwd) / f"{job_id}.json"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temp_path = Path(handle.name)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _tail_text(value: str, limit: int = OUTPUT_TAIL_LIMIT) -> str:
    return value[-limit:] if len(value) > limit else value


def _json_or_default(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(default)


def _choose_docs(cwd: Path, framework_root: Path) -> dict[str, Optional[str]]:
    local_doc_root = _artifact_root(cwd)
    framework_doc_root = _artifact_root(framework_root)
    local_agents = local_doc_root / "AGENTS.md"
    local_codex = local_doc_root / "CODEX.md"
    framework_agents = framework_doc_root / "AGENTS.md"
    framework_codex = framework_doc_root / "CODEX.md"

    chosen_agents = local_agents if local_agents.exists() else framework_agents if framework_agents.exists() else None
    chosen_main = local_codex if local_codex.exists() else framework_codex if framework_codex.exists() else None
    return {
        "local_agents": str(local_agents) if local_agents.exists() else None,
        "local_codex": str(local_codex) if local_codex.exists() else None,
        "framework_agents": str(framework_agents) if framework_agents.exists() else None,
        "framework_codex": str(framework_codex) if framework_codex.exists() else None,
        "chosen_agents": str(chosen_agents) if chosen_agents else None,
        "chosen_main": str(chosen_main) if chosen_main else None,
    }


def _normalize_args(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    alias_map = {
        "conversationId": "conversation_id",
        "approval-policy": "approval_policy",
        "developer-instructions": "developer_instructions",
        "artifact-path": "artifact_path",
        "framework-root": "framework_root",
        "timeout-seconds": "timeout_seconds",
        "allow-local-fallback": "allow_local_fallback",
    }
    for source, target in alias_map.items():
        if source in normalized and target not in normalized:
            normalized[target] = normalized[source]
    return normalized


def _build_prompt(
    *,
    prompt: str,
    cwd: Path,
    framework_root: Path,
    artifact_path: Optional[str],
    developer_instructions: Optional[str],
    task_marker: Optional[str],
) -> Tuple[str, dict[str, Optional[str]]]:
    docs = _choose_docs(cwd, framework_root)
    _, prompt_body = _extract_task_marker(prompt)
    local_artifact_root = _artifact_root(cwd)

    lines = []
    task_marker_line = _render_task_marker(task_marker)
    if task_marker_line:
        lines.append(task_marker_line)
    lines.append("$codex-reviewer-workflow")
    lines.append("你是 multi-codex 架构中的审查 Codex。")
    lines.append("会话与续聊由 MCP wrapper 管理：不要猜测、编造或手工回填 conversation_id。")
    lines.append(f"当前目标仓库：{cwd}")
    lines.append(f"审查产物目录：{local_artifact_root}")
    if artifact_path:
        lines.append(f"本轮优先产物路径：{artifact_path}")
    lines.append("文档读取顺序：")
    if docs["chosen_agents"]:
        lines.append(f"1. 先读取 .codex/AGENTS.md：{docs['chosen_agents']}")
    else:
        lines.append("1. 未找到 .codex/AGENTS.md；需要在输出中声明降级。")
    if docs["chosen_main"]:
        lines.append(f"2. 再读取 .codex/CODEX.md：{docs['chosen_main']}")
    else:
        lines.append("2. 未找到 .codex/CODEX.md；需要在输出中声明降级。")
    lines.append("如果项目内缺少这些文档，可以读取上面的框架 .codex 文档作为降级方案，但必须明确说明。")
    lines.append("只允许把 reviewer 产物写入项目本地 .codex/ 目录，不直接修改业务代码，除非显式覆盖。")
    if developer_instructions:
        lines.append("补充开发者约束：")
        lines.append(developer_instructions.strip())
    lines.append("以下是主 Codex 传入的任务：")
    lines.append(prompt_body.strip() or prompt.strip())
    return "\n".join(lines).strip(), docs


def _extract_thread_id_from_events(output: str) -> Optional[str]:
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        thread_id = _find_nested_string(event, "thread_id")
        if thread_id:
            return thread_id
    return None


def _find_nested_string(node: Any, key: str) -> Optional[str]:
    if isinstance(node, dict):
        if key in node and isinstance(node[key], str) and node[key]:
            return node[key]
        for value in node.values():
            found = _find_nested_string(value, key)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_nested_string(item, key)
            if found:
                return found
    return None


def _lookup_thread_id_from_state(cwd: Path, start_time: float) -> Optional[str]:
    database_path = _home_path(".codex", "state_5.sqlite")
    if not database_path.exists():
        return None
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(database_path)
        cursor = connection.cursor()
        min_created_at = int(start_time) - 5
        rows = cursor.execute(
            """
            SELECT id
            FROM threads
            WHERE cwd = ?
              AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (str(cwd), min_created_at),
        ).fetchall()
        return rows[0][0] if rows else None
    except sqlite3.Error:
        return None
    finally:
        try:
            if connection is not None:
                connection.close()
        except Exception:
            pass


def _session_file(cwd: Path) -> Path:
    return _artifact_root(cwd) / "codex-reviewer-sessions.json"


def _ensure_artifact_root(cwd: Path) -> Path:
    artifact_root = _artifact_root(cwd)
    artifact_root.mkdir(parents=True, exist_ok=True)
    return artifact_root


def _load_session_data(path: Path) -> dict[str, Any]:
    return _json_or_default(path, {"updated_at": _now_shanghai(), "sessions": []})


def _match_session(session: dict[str, Any], task_marker: Optional[str], conversation_id: Optional[str]) -> bool:
    normalized_marker = _normalize_task_marker(task_marker)
    if normalized_marker and session.get("task_marker") == normalized_marker:
        return True
    if conversation_id and session.get("conversation_id") == conversation_id:
        return True
    return False


def _find_session(cwd: Path, task_marker: Optional[str] = None, conversation_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    sessions = _load_session_data(_session_file(cwd)).get("sessions", [])
    if task_marker or conversation_id:
        for session in reversed(sessions):
            if _match_session(session, task_marker, conversation_id):
                return session
        return None
    if sessions:
        return sessions[-1]
    return None


def _save_session(
    *,
    cwd: Path,
    task_marker: Optional[str],
    conversation_id: Optional[str],
    description: str,
    status: str,
    artifact_paths: list[str],
    review_mode: Optional[str] = None,
    gate_passed: bool = False,
    blocking_reason: Optional[str] = None,
) -> dict[str, Any]:
    normalized_marker = _normalize_task_marker(task_marker)
    session_path = _session_file(cwd)
    data = _load_session_data(session_path)
    sessions = data.setdefault("sessions", [])
    existing = None
    for session in sessions:
        if _match_session(session, normalized_marker, conversation_id):
            existing = session
            break

    if existing is None:
        existing = {
            "task_marker": normalized_marker,
            "conversation_id": conversation_id,
            "created_at": _now_shanghai(),
        }
        sessions.append(existing)

    merged_artifacts = list(dict.fromkeys([*existing.get("artifact_paths", []), *artifact_paths]))
    existing.update(
        {
            "task_marker": existing.get("task_marker") or normalized_marker,
            "conversation_id": conversation_id or existing.get("conversation_id"),
            "updated_at": _now_shanghai(),
            "last_activity_at": _now_shanghai(),
            "cwd": str(cwd),
            "description": description[:280],
            "status": status,
            "artifact_paths": merged_artifacts,
            "review_mode": review_mode or existing.get("review_mode") or DEFAULT_REVIEW_MODE,
            "gate_passed": bool(gate_passed),
            "last_blocking_reason": blocking_reason,
            "last_artifact_path": merged_artifacts[-1] if merged_artifacts else None,
        }
    )
    data["updated_at"] = _now_shanghai()
    _write_json_atomic(session_path, data)
    return existing


def _new_job_id() -> str:
    return uuid.uuid4().hex


def _load_job(cwd: Path, job_id: str) -> Optional[dict[str, Any]]:
    path = _job_file(cwd, job_id)
    if not path.exists():
        return None
    return _json_or_default(path, {})


def _job_artifact_exists(cwd: Path, artifact_paths: list[str]) -> bool:
    if not artifact_paths:
        return False
    return all((cwd / artifact_path).resolve().exists() for artifact_path in artifact_paths)


def _job_output_view(job: dict[str, Any]) -> dict[str, Any]:
    cwd = Path(job["cwd"]).expanduser().resolve()
    artifact_paths = list(job.get("artifact_paths", []))
    return {
        "server": SERVER_NAME,
        "review_mode": job.get("review_mode", DEFAULT_REVIEW_MODE),
        "job_id": job.get("job_id"),
        "tool_name": job.get("tool_name"),
        "task_marker": job.get("task_marker"),
        "conversation_id": job.get("conversation_id"),
        "status": job.get("status"),
        "pid": job.get("pid"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "heartbeat_at": job.get("heartbeat_at"),
        "updated_at": job.get("updated_at"),
        "artifact_paths": artifact_paths,
        "artifact_exists": _job_artifact_exists(cwd, artifact_paths),
        "returncode": job.get("returncode"),
        "timed_out": bool(job.get("timed_out", False)),
        "assistant_message": job.get("assistant_message", ""),
        "stdout_tail": job.get("stdout_tail", ""),
        "stderr_tail": job.get("stderr_tail", ""),
    }


def _create_job_record(
    *,
    tool_name: str,
    cwd: Path,
    framework_root: Path,
    task_marker: Optional[str],
    conversation_id: Optional[str],
    prompt: str,
    artifact_path: Optional[str],
    developer_instructions: Optional[str],
    model: Optional[str],
    profile: Optional[str],
    sandbox: Optional[str],
    approval_policy: Optional[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    now_epoch = _now_epoch()
    now_readable = _now_shanghai()
    normalized_marker = _normalize_task_marker(task_marker)
    artifact_paths = [artifact_path] if artifact_path else []
    return {
        "job_id": _new_job_id(),
        "tool_name": tool_name,
        "cwd": str(cwd),
        "framework_root": str(framework_root),
        "task_marker": normalized_marker,
        "conversation_id": conversation_id,
        "prompt": prompt,
        "artifact_path": artifact_path,
        "artifact_paths": artifact_paths,
        "developer_instructions": developer_instructions,
        "model": model,
        "profile": profile,
        "sandbox": sandbox,
        "approval_policy": approval_policy,
        "timeout_seconds": timeout_seconds,
        "status": "queued",
        "review_mode": DEFAULT_REVIEW_MODE,
        "pid": None,
        "created_at": now_readable,
        "created_at_unix": now_epoch,
        "started_at": None,
        "started_at_unix": None,
        "heartbeat_at": None,
        "heartbeat_at_unix": None,
        "updated_at": now_readable,
        "updated_at_unix": now_epoch,
        "returncode": None,
        "timed_out": False,
        "assistant_message": "",
        "stdout_tail": "",
        "stderr_tail": "",
    }


def _save_job(cwd: Path, job: dict[str, Any]) -> dict[str, Any]:
    job = dict(job)
    job["task_marker"] = _normalize_task_marker(job.get("task_marker"))
    now_epoch = _now_epoch()
    job.setdefault("created_at", _now_shanghai())
    job.setdefault("created_at_unix", now_epoch)
    job["updated_at"] = _now_shanghai()
    job["updated_at_unix"] = now_epoch
    _write_json_atomic(_job_file(cwd, str(job["job_id"])), job)
    return job


def _update_job(cwd: Path, job_id: str, **updates: Any) -> dict[str, Any]:
    existing = _load_job(cwd, job_id)
    if not existing:
        raise FileNotFoundError(f"job not found: {job_id}")
    now_epoch = _now_epoch()
    now_readable = _now_shanghai()
    if "heartbeat_at" in updates and updates["heartbeat_at"] is None:
        updates["heartbeat_at_unix"] = None
    elif "heartbeat_at" in updates:
        updates.setdefault("heartbeat_at_unix", now_epoch)
    if "started_at" in updates and updates["started_at"] is None:
        updates["started_at_unix"] = None
    elif "started_at" in updates:
        updates.setdefault("started_at_unix", now_epoch)
    if "task_marker" in updates:
        updates["task_marker"] = _normalize_task_marker(updates["task_marker"])
    existing.update(updates)
    existing["updated_at"] = now_readable
    existing["updated_at_unix"] = now_epoch
    _write_json_atomic(_job_file(cwd, job_id), existing)
    return existing


def _list_jobs(cwd: Path) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    jobs_dir = _jobs_root(cwd)
    if not jobs_dir.exists():
        return jobs
    for path in jobs_dir.glob("*.json"):
        payload = _json_or_default(path, {})
        if payload.get("job_id"):
            jobs.append(payload)
    jobs.sort(key=lambda job: (job.get("updated_at_unix", 0), job.get("created_at_unix", 0), str(job.get("job_id", ""))))
    return jobs


def _find_job(
    cwd: Path,
    *,
    job_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    task_marker: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    if job_id:
        return _load_job(cwd, job_id)

    jobs = _list_jobs(cwd)
    if conversation_id:
        matches = [job for job in jobs if job.get("conversation_id") == conversation_id]
        if matches:
            return matches[-1]

    normalized_marker = _normalize_task_marker(task_marker)
    if normalized_marker:
        matches = [job for job in jobs if job.get("task_marker") == normalized_marker]
        if matches:
            return matches[-1]

    return None


def _find_active_job(
    cwd: Path,
    *,
    tool_name: str,
    task_marker: Optional[str],
    conversation_id: Optional[str],
    artifact_path: Optional[str],
) -> Optional[dict[str, Any]]:
    normalized_marker = _normalize_task_marker(task_marker)
    jobs = _list_jobs(cwd)
    for job in reversed(jobs):
        if job.get("tool_name") != tool_name:
            continue
        if job.get("status") not in ACTIVE_JOB_STATUSES:
            continue
        if (job.get("artifact_path") or None) != artifact_path:
            continue
        if normalized_marker and job.get("task_marker") == normalized_marker:
            return job
        if not normalized_marker and conversation_id and job.get("conversation_id") == conversation_id:
            return job
    return None


def _approval_flags(approval_policy: Optional[str], sandbox: Optional[str]) -> list[str]:
    normalized_approval = (approval_policy or "").strip()
    normalized_sandbox = (sandbox or "").strip()
    if normalized_approval == "on-request" and normalized_sandbox in {"", "workspace-write"}:
        return ["--full-auto"]
    if normalized_approval == "never" and normalized_sandbox == "danger-full-access":
        return ["--dangerously-bypass-approvals-and-sandbox"]
    flags: list[str] = []
    if normalized_sandbox:
        flags.extend(["--sandbox", normalized_sandbox])
    if normalized_approval and not flags:
        flags.extend(["-c", f'approval_policy="{normalized_approval}"'])
    return flags


def _run_codex_command(
    *,
    cmd: list[str],
    cwd: Path,
    timeout_seconds: int,
    heartbeat_callback: Optional[Callable[[], None]] = None,
) -> Tuple[Optional[int], str, str, bool]:
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    timed_out = False
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_seconds)
            try:
                stdout, stderr = process.communicate(timeout=min(JOB_HEARTBEAT_SECONDS, remaining))
                return process.returncode, stdout, stderr, timed_out
            except subprocess.TimeoutExpired:
                if heartbeat_callback is not None:
                    heartbeat_callback()
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, stderr = process.communicate()
        return None, stdout, stderr, timed_out


def _build_exec_command(
    *,
    codex_binary: str,
    prompt: str,
    output_path: Path,
    cwd: Path,
    framework_root: Path,
    model: Optional[str],
    profile: Optional[str],
    sandbox: Optional[str],
    approval_policy: Optional[str],
) -> list[str]:
    cmd = [codex_binary, "exec", "--json", "--skip-git-repo-check", "-o", str(output_path), "-C", str(cwd)]
    cmd.extend(_approval_flags(approval_policy, sandbox))
    if model:
        cmd.extend(["--model", model])
    if profile:
        cmd.extend(["--profile", profile])
    if framework_root != cwd:
        cmd.extend(["--add-dir", str(framework_root)])
    cmd.append(prompt)
    return cmd


def _build_resume_command(
    *,
    codex_binary: str,
    conversation_id: str,
    prompt: str,
    output_path: Path,
    cwd: Path,
    model: Optional[str],
    profile: Optional[str],
    sandbox: Optional[str],
    approval_policy: Optional[str],
) -> list[str]:
    cmd = [
        codex_binary,
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        "-o",
        str(output_path),
    ]
    cmd.extend(_approval_flags(approval_policy, sandbox))
    if model:
        cmd.extend(["--model", model])
    # `codex exec resume` inherits the subprocess working directory and currently
    # exposes a narrower flag surface than `codex exec`, so avoid passing exec-only
    # options like `--cd` / `--profile` here.
    cmd.append(conversation_id)
    cmd.append(prompt)
    return cmd


def _last_message(output_path: Path) -> str:
    if not output_path.exists():
        return ""
    return output_path.read_text(encoding="utf-8").strip()


def _base_payload(
    *,
    cmd: list[str],
    cwd: Path,
    docs: dict[str, Optional[str]],
    task_marker: Optional[str],
    artifact_path: Optional[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    return {
        "server": SERVER_NAME,
        "cwd": str(cwd),
        "task_marker": task_marker,
        "artifact_path": artifact_path,
        "timeout_seconds": timeout_seconds,
        "command": cmd,
        "documents": docs,
    }


def _job_status_to_blocking_reason(status: Optional[str]) -> Optional[str]:
    if status in ACTIVE_JOB_STATUSES:
        return "review_in_progress"
    if status == "timeout":
        return "review_timeout"
    if status in {"error", "failed", "stale"}:
        return "review_error"
    return None


def _watch_worker_process(process: subprocess.Popen[Any], job_id: str) -> None:
    try:
        returncode = process.wait()
        _diagnostic_log("worker_reaped", job_id=job_id, pid=process.pid, returncode=returncode)
    except Exception as exc:  # noqa: BLE001
        _diagnostic_log("worker_reap_failed", job_id=job_id, pid=process.pid, error=str(exc))


def _start_worker_reaper(process: subprocess.Popen[Any], job_id: str) -> None:
    Thread(target=_watch_worker_process, args=(process, job_id), daemon=True).start()


def _spawn_job_worker(cwd: Path, job_id: str) -> subprocess.Popen[Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "run-job",
        "--cwd",
        str(cwd),
        "--job-id",
        job_id,
    ]
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _start_worker_reaper(process, job_id)
    _diagnostic_log("worker_spawned", job_id=job_id, pid=process.pid, cwd=str(cwd))
    return process


def _run_job_janitor(cwd: Path) -> dict[str, int]:
    _ensure_artifact_root(cwd)
    now_epoch = _now_epoch()
    stale_count = 0
    failed_count = 0

    for job in _list_jobs(cwd):
        status = job.get("status")
        if status not in ACTIVE_JOB_STATUSES:
            continue

        pid = job.get("pid")
        if isinstance(pid, int) and pid > 0 and not _pid_exists(pid):
            updated = _update_job(
                cwd,
                str(job["job_id"]),
                status="stale",
                heartbeat_at=_now_shanghai(),
                stderr_tail=_tail_text(f"{job.get('stderr_tail', '')}\njanitor: recorded worker pid is no longer alive".strip()),
            )
            _save_session(
                cwd=cwd,
                task_marker=updated.get("task_marker"),
                conversation_id=updated.get("conversation_id"),
                description=updated.get("prompt", ""),
                status="stale",
                artifact_paths=list(updated.get("artifact_paths", [])),
                review_mode=updated.get("review_mode"),
                gate_passed=False,
                blocking_reason="review_error",
            )
            stale_count += 1
            continue

        if status == "queued" and not pid:
            created_at_unix = float(job.get("created_at_unix") or 0)
            if created_at_unix and (now_epoch - created_at_unix) >= JOB_QUEUE_STALE_SECONDS:
                updated = _update_job(
                    cwd,
                    str(job["job_id"]),
                    status="failed",
                    stderr_tail=_tail_text(f"{job.get('stderr_tail', '')}\njanitor: queued job never reported a worker pid".strip()),
                )
                _save_session(
                    cwd=cwd,
                    task_marker=updated.get("task_marker"),
                    conversation_id=updated.get("conversation_id"),
                    description=updated.get("prompt", ""),
                    status="failed",
                    artifact_paths=list(updated.get("artifact_paths", [])),
                    review_mode=updated.get("review_mode"),
                    gate_passed=False,
                    blocking_reason="review_error",
                )
                failed_count += 1

    if stale_count or failed_count:
        _diagnostic_log("job_janitor", cwd=str(cwd), stale_count=stale_count, failed_count=failed_count)
    return {"stale": stale_count, "failed": failed_count}


def _review_status_payload(
    *,
    cwd: Path,
    job_id: Optional[str],
    conversation_id: Optional[str],
    task_marker: Optional[str],
) -> dict[str, Any]:
    _run_job_janitor(cwd)
    job = _find_job(cwd, job_id=job_id, conversation_id=conversation_id, task_marker=task_marker)
    if not job:
        raise ValueError("review job not found")
    return _job_output_view(job)


def _review_gate_payload(
    *,
    cwd: Path,
    artifact_path: Optional[str],
    conversation_id: Optional[str],
    task_marker: Optional[str],
    allow_local_fallback: bool,
) -> dict[str, Any]:
    _run_job_janitor(cwd)
    artifact_rel = artifact_path or DEFAULT_REVIEW_ARTIFACT
    artifact_abs = (cwd / artifact_rel).resolve()
    artifact_exists = artifact_abs.exists()
    job = _find_job(cwd, conversation_id=conversation_id, task_marker=task_marker)
    session = _find_session(cwd, task_marker=task_marker, conversation_id=conversation_id)
    session_status = "missing"
    resolved_conversation_id = conversation_id
    review_mode: Optional[str] = None
    blocking_reason: Optional[str] = None
    gate_passed = False

    if job:
        session_status = job.get("status", "missing")
        resolved_conversation_id = job.get("conversation_id") or resolved_conversation_id
        review_mode = job.get("review_mode")
    elif session:
        session_status = session.get("status", "missing")
        resolved_conversation_id = session.get("conversation_id") or resolved_conversation_id
        review_mode = session.get("review_mode")

    if session_status in ACTIVE_JOB_STATUSES:
        blocking_reason = "review_in_progress"
    elif session_status == "completed" and artifact_exists:
        gate_passed = True
        review_mode = review_mode or (DEFAULT_REVIEW_MODE if resolved_conversation_id else "local_fallback")
    elif allow_local_fallback and artifact_exists:
        gate_passed = True
        review_mode = "local_fallback"
        session_status = "completed"
    elif session_status in {"timeout", "error", "failed", "stale"}:
        blocking_reason = _job_status_to_blocking_reason(session_status)
    elif not artifact_exists:
        blocking_reason = "review_artifact_missing"
    else:
        blocking_reason = "review_session_missing"

    return {
        "server": SERVER_NAME,
        "cwd": str(cwd),
        "job_id": job.get("job_id") if job else None,
        "artifact_path": artifact_rel,
        "artifact_exists": artifact_exists,
        "conversation_id": resolved_conversation_id,
        "task_marker": _normalize_task_marker(task_marker) or (session.get("task_marker") if session else None),
        "review_mode": review_mode or "unknown",
        "session_status": session_status,
        "gate_passed": gate_passed,
        "blocking_reason": blocking_reason,
        "allow_local_fallback": allow_local_fallback,
    }


def _queue_job(
    *,
    tool_name: str,
    prompt: str,
    cwd: Path,
    framework_root: Path,
    task_marker: Optional[str],
    conversation_id: Optional[str],
    artifact_path: Optional[str],
    developer_instructions: Optional[str],
    timeout_seconds: int,
    model: Optional[str],
    profile: Optional[str],
    sandbox: Optional[str],
    approval_policy: Optional[str],
    dry_run: bool,
) -> dict[str, Any]:
    normalized_marker = _normalize_task_marker(task_marker)
    _ensure_artifact_root(cwd)
    _run_job_janitor(cwd)

    docs = _choose_docs(cwd, framework_root)
    with tempfile.TemporaryDirectory(prefix=f"codex-reviewer-{tool_name}-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.txt"
        if tool_name == "codex":
            wrapped_prompt, docs = _build_prompt(
                prompt=prompt,
                cwd=cwd,
                framework_root=framework_root,
                artifact_path=artifact_path,
                developer_instructions=developer_instructions,
                task_marker=normalized_marker,
            )
            cmd = _build_exec_command(
                codex_binary=_codex_binary(),
                prompt=wrapped_prompt,
                output_path=output_path,
                cwd=cwd,
                framework_root=framework_root,
                model=model,
                profile=profile,
                sandbox=sandbox,
                approval_policy=approval_policy,
            )
        else:
            cmd = _build_resume_command(
                codex_binary=_codex_binary(),
                conversation_id=str(conversation_id),
                prompt=prompt,
                output_path=output_path,
                cwd=cwd,
                model=model,
                profile=profile,
                sandbox=sandbox,
                approval_policy=approval_policy,
            )

    payload = _base_payload(
        cmd=cmd,
        cwd=cwd,
        docs=docs,
        task_marker=normalized_marker,
        artifact_path=artifact_path,
        timeout_seconds=timeout_seconds,
    )
    payload["conversation_id"] = conversation_id
    if dry_run:
        payload.update({"status": "dry_run", "assistant_message": "", "review_mode": DEFAULT_REVIEW_MODE})
        return _tool_result(payload)

    existing = _find_active_job(
        cwd,
        tool_name=tool_name,
        task_marker=normalized_marker,
        conversation_id=conversation_id,
        artifact_path=artifact_path,
    )
    if existing:
        payload.update(_job_output_view(existing))
        payload["reused_existing_job"] = True
        return _tool_result(payload)

    job = _create_job_record(
        tool_name=tool_name,
        cwd=cwd,
        framework_root=framework_root,
        task_marker=normalized_marker,
        conversation_id=conversation_id,
        prompt=prompt,
        artifact_path=artifact_path,
        developer_instructions=developer_instructions,
        model=model,
        profile=profile,
        sandbox=sandbox,
        approval_policy=approval_policy,
        timeout_seconds=timeout_seconds,
    )
    _save_job(cwd, job)
    _save_session(
        cwd=cwd,
        task_marker=normalized_marker,
        conversation_id=conversation_id,
        description=prompt,
        status="queued",
        artifact_paths=list(job.get("artifact_paths", [])),
        review_mode=DEFAULT_REVIEW_MODE,
        gate_passed=False,
        blocking_reason="review_in_progress",
    )

    try:
        _spawn_job_worker(cwd, str(job["job_id"]))
    except OSError as exc:
        failed_job = _update_job(
            cwd,
            str(job["job_id"]),
            status="failed",
            stderr_tail=_tail_text(str(exc)),
        )
        _save_session(
            cwd=cwd,
            task_marker=failed_job.get("task_marker"),
            conversation_id=failed_job.get("conversation_id"),
            description=prompt,
            status="failed",
            artifact_paths=list(failed_job.get("artifact_paths", [])),
            review_mode=DEFAULT_REVIEW_MODE,
            gate_passed=False,
            blocking_reason="review_error",
        )
        payload.update(_job_output_view(failed_job))
        payload["reused_existing_job"] = False
        return _tool_result(payload, is_error=True)

    payload.update(_job_output_view(job))
    payload["reused_existing_job"] = False
    return _tool_result(payload)


def _handle_codex(arguments: dict[str, Any]) -> dict[str, Any]:
    args = _normalize_args(arguments)
    prompt = str(args["prompt"])
    cwd = Path(args.get("cwd") or os.getcwd()).expanduser().resolve()
    framework_root = Path(args.get("framework_root") or _framework_root()).expanduser().resolve()
    task_marker, _ = _extract_task_marker(prompt)
    task_marker = args.get("task_marker") or task_marker
    artifact_path = args.get("artifact_path")
    developer_instructions = args.get("developer_instructions")
    timeout_seconds = int(args.get("timeout_seconds") or 240)
    model = args.get("model")
    profile = args.get("profile")
    sandbox = args.get("sandbox") or "workspace-write"
    approval_policy = args.get("approval_policy") or "on-request"
    dry_run = bool(args.get("dry_run"))
    return _queue_job(
        tool_name="codex",
        prompt=prompt,
        cwd=cwd,
        framework_root=framework_root,
        task_marker=task_marker,
        conversation_id=None,
        artifact_path=artifact_path,
        developer_instructions=developer_instructions,
        timeout_seconds=timeout_seconds,
        model=model,
        profile=profile,
        sandbox=sandbox,
        approval_policy=approval_policy,
        dry_run=dry_run,
    )


def _handle_codex_reply(arguments: dict[str, Any]) -> dict[str, Any]:
    args = _normalize_args(arguments)
    prompt = str(args["prompt"])
    conversation_id = args.get("conversation_id")
    if not conversation_id:
        raise ValueError("conversation_id is required")

    cwd = Path(args.get("cwd") or os.getcwd()).expanduser().resolve()
    framework_root = Path(args.get("framework_root") or _framework_root()).expanduser().resolve()
    task_marker, _ = _extract_task_marker(prompt)
    artifact_path = args.get("artifact_path")
    timeout_seconds = int(args.get("timeout_seconds") or 240)
    model = args.get("model")
    profile = args.get("profile")
    sandbox = args.get("sandbox") or "workspace-write"
    approval_policy = args.get("approval_policy") or "on-request"
    dry_run = bool(args.get("dry_run"))
    return _queue_job(
        tool_name="codex_reply",
        prompt=prompt,
        cwd=cwd,
        framework_root=framework_root,
        task_marker=task_marker,
        conversation_id=str(conversation_id),
        artifact_path=artifact_path,
        developer_instructions=None,
        timeout_seconds=timeout_seconds,
        model=model,
        profile=profile,
        sandbox=sandbox,
        approval_policy=approval_policy,
        dry_run=dry_run,
    )


def _handle_review_status(arguments: dict[str, Any]) -> dict[str, Any]:
    args = _normalize_args(arguments)
    cwd = Path(args.get("cwd") or os.getcwd()).expanduser().resolve()
    payload = _review_status_payload(
        cwd=cwd,
        job_id=args.get("job_id"),
        conversation_id=args.get("conversation_id"),
        task_marker=args.get("task_marker"),
    )
    return _tool_result(payload)


def _handle_review_gate(arguments: dict[str, Any]) -> dict[str, Any]:
    args = _normalize_args(arguments)
    cwd = Path(args.get("cwd") or os.getcwd()).expanduser().resolve()
    artifact_path = args.get("artifact_path")
    conversation_id = args.get("conversation_id")
    task_marker = args.get("task_marker")
    allow_local_fallback = bool(args.get("allow_local_fallback", True))
    payload = _review_gate_payload(
        cwd=cwd,
        artifact_path=artifact_path,
        conversation_id=conversation_id,
        task_marker=task_marker,
        allow_local_fallback=allow_local_fallback,
    )
    return _tool_result(payload, is_error=not payload["gate_passed"])


def _run_job_worker(cwd: Path, job_id: str) -> int:
    _ensure_artifact_root(cwd)
    job = _load_job(cwd, job_id)
    if not job:
        raise FileNotFoundError(f"job not found: {job_id}")

    framework_root = Path(job.get("framework_root") or _framework_root()).expanduser().resolve()
    tool_name = str(job["tool_name"])
    prompt = str(job["prompt"])
    artifact_paths = list(job.get("artifact_paths", []))

    _update_job(
        cwd,
        job_id,
        status="running",
        pid=os.getpid(),
        started_at=_now_shanghai(),
        heartbeat_at=_now_shanghai(),
    )
    _save_session(
        cwd=cwd,
        task_marker=job.get("task_marker"),
        conversation_id=job.get("conversation_id"),
        description=prompt,
        status="running",
        artifact_paths=artifact_paths,
        review_mode=job.get("review_mode"),
        gate_passed=False,
        blocking_reason="review_in_progress",
    )

    def _touch_heartbeat() -> None:
        try:
            _update_job(cwd, job_id, heartbeat_at=_now_shanghai())
        except FileNotFoundError:
            return

    try:
        with tempfile.TemporaryDirectory(prefix=f"codex-reviewer-worker-{tool_name}-") as temp_dir:
            output_path = Path(temp_dir) / "last-message.txt"
            timeout_seconds = int(job.get("timeout_seconds") or 240)
            if tool_name == "codex":
                wrapped_prompt, _ = _build_prompt(
                    prompt=prompt,
                    cwd=cwd,
                    framework_root=framework_root,
                    artifact_path=job.get("artifact_path"),
                    developer_instructions=job.get("developer_instructions"),
                    task_marker=job.get("task_marker"),
                )
                cmd = _build_exec_command(
                    codex_binary=_codex_binary(),
                    prompt=wrapped_prompt,
                    output_path=output_path,
                    cwd=cwd,
                    framework_root=framework_root,
                    model=job.get("model"),
                    profile=job.get("profile"),
                    sandbox=job.get("sandbox"),
                    approval_policy=job.get("approval_policy"),
                )
                start_time = time.time()
                returncode, stdout, stderr, timed_out = _run_codex_command(
                    cmd=cmd,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                    heartbeat_callback=_touch_heartbeat,
                )
                conversation_id = _extract_thread_id_from_events(stdout) or _lookup_thread_id_from_state(cwd, start_time)
            elif tool_name == "codex_reply":
                cmd = _build_resume_command(
                    codex_binary=_codex_binary(),
                    conversation_id=str(job.get("conversation_id")),
                    prompt=prompt,
                    output_path=output_path,
                    cwd=cwd,
                    model=job.get("model"),
                    profile=job.get("profile"),
                    sandbox=job.get("sandbox"),
                    approval_policy=job.get("approval_policy"),
                )
                returncode, stdout, stderr, timed_out = _run_codex_command(
                    cmd=cmd,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                    heartbeat_callback=_touch_heartbeat,
                )
                conversation_id = job.get("conversation_id")
            else:
                raise ValueError(f"unsupported job tool: {tool_name}")

            assistant_message = _last_message(output_path)
            status = "timeout" if timed_out else "completed" if returncode == 0 else "error"
            updated = _update_job(
                cwd,
                job_id,
                conversation_id=conversation_id,
                status=status,
                heartbeat_at=_now_shanghai(),
                returncode=returncode,
                timed_out=timed_out,
                assistant_message=assistant_message,
                stdout_tail=_tail_text(stdout),
                stderr_tail=_tail_text(stderr),
            )
            _save_session(
                cwd=cwd,
                task_marker=updated.get("task_marker"),
                conversation_id=updated.get("conversation_id"),
                description=prompt,
                status=status,
                artifact_paths=list(updated.get("artifact_paths", [])),
                review_mode=updated.get("review_mode"),
                gate_passed=False,
                blocking_reason=_job_status_to_blocking_reason(status),
            )
            return 0 if status == "completed" else 1
    except Exception:  # noqa: BLE001
        trace = traceback.format_exc()
        updated = _update_job(
            cwd,
            job_id,
            status="error",
            heartbeat_at=_now_shanghai(),
            timed_out=False,
            stderr_tail=_tail_text(trace),
        )
        _save_session(
            cwd=cwd,
            task_marker=updated.get("task_marker"),
            conversation_id=updated.get("conversation_id"),
            description=prompt,
            status="error",
            artifact_paths=list(updated.get("artifact_paths", [])),
            review_mode=updated.get("review_mode"),
            gate_passed=False,
            blocking_reason="review_error",
        )
        _diagnostic_log("worker_failed", job_id=job_id, cwd=str(cwd), traceback=trace)
        return 1


def _parse_elapsed_seconds(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    days = 0
    time_part = value
    if "-" in value:
        day_part, time_part = value.split("-", 1)
        if day_part.isdigit():
            days = int(day_part)
    parts = time_part.split(":")
    if not all(part.isdigit() for part in parts):
        return None
    if len(parts) == 3:
        hours, minutes, seconds = [int(part) for part in parts]
    elif len(parts) == 2:
        hours = 0
        minutes, seconds = [int(part) for part in parts]
    elif len(parts) == 1:
        hours = 0
        minutes = 0
        seconds = int(parts[0])
    else:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _parse_ps_start(value: str) -> Optional[float]:
    normalized = " ".join(value.split())
    try:
        parsed = time.strptime(normalized, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return time.mktime(parsed)


def _run_process_command(cmd: list[str]) -> tuple[bool, str, str]:
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except (OSError, ValueError) as exc:
        return False, "", str(exc)
    return completed.returncode == 0, completed.stdout, completed.stderr


def _list_processes() -> Tuple[bool, list[ProcessInfo], Optional[str]]:
    if os.name == "nt":
        return False, [], "process inspection is only available on POSIX hosts in this repository"

    success, stdout, stderr = _run_process_command(["ps", "-Ao", "pid=,ppid=,stat=,etime=,lstart=,command="])
    if not success:
        return False, [], stderr.strip() or "failed to execute ps"

    processes: list[ProcessInfo] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 9)
        if len(parts) < 10:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        state = parts[2]
        elapsed_seconds = _parse_elapsed_seconds(parts[3])
        started_at = _parse_ps_start(" ".join(parts[4:9]))
        command = parts[9]
        processes.append(
            ProcessInfo(
                pid=pid,
                ppid=ppid,
                command=command,
                state=state,
                elapsed_seconds=elapsed_seconds,
                started_at=started_at,
            )
        )
    return True, processes, None


def _is_app_server_process(process: ProcessInfo) -> bool:
    return any(marker in process.command for marker in HOST_COMMAND_MARKERS)


def _is_reviewer_process(process: ProcessInfo) -> bool:
    return "codex_reviewer_mcp.py" in process.command


def _is_diagnostic_parent(process: ProcessInfo) -> bool:
    return all(marker in process.command for marker in DIAGNOSTIC_PARENT_MARKERS)


def _classify_host_process(process: ProcessInfo, reference_timestamp: Optional[float]) -> dict[str, Any]:
    restart_required = bool(reference_timestamp and process.started_at and process.started_at < reference_timestamp)
    return {
        "pid": process.pid,
        "ppid": process.ppid,
        "command": process.command,
        "started_at": _format_epoch(process.started_at),
        "elapsed_seconds": process.elapsed_seconds,
        "restart_required": restart_required,
    }


def _classify_reviewer_process(
    process: ProcessInfo,
    by_pid: dict[int, ProcessInfo],
    reference_timestamp: Optional[float],
    stale_seconds: int = REVIEWER_STALE_SECONDS,
) -> dict[str, Any]:
    parent = by_pid.get(process.ppid)
    elapsed = process.elapsed_seconds or 0
    result = {
        "pid": process.pid,
        "ppid": process.ppid,
        "command": process.command,
        "elapsed_seconds": process.elapsed_seconds,
        "started_at": _format_epoch(process.started_at),
        "parent_command": parent.command if parent else None,
        "status": "healthy_attached",
        "cleanable": False,
        "reason": "reviewer attached to a live host",
    }

    if parent is None or process.ppid <= 1:
        if elapsed >= stale_seconds:
            result.update({"status": "stale_orphan", "cleanable": True, "reason": "reviewer process has no live parent"})
        else:
            result.update({"status": "recent_orphan", "reason": "reviewer parent already exited"})
        return result

    if _is_diagnostic_parent(parent):
        if elapsed >= DIAGNOSTIC_CHILD_SECONDS:
            result.update(
                {
                    "status": "stale_diagnostic_child",
                    "cleanable": True,
                    "reason": "reviewer process is attached to a diagnostic launcher and stayed alive too long",
                }
            )
        else:
            result.update({"status": "diagnostic_child", "reason": "reviewer process belongs to a short-lived diagnostic probe"})
        return result

    if _is_app_server_process(parent):
        if reference_timestamp and parent.started_at and parent.started_at < reference_timestamp:
            result.update(
                {
                    "status": "attached_to_outdated_host",
                    "reason": "reviewer is attached to an app-server started before the latest config/script update",
                }
            )
            return result
        if elapsed >= stale_seconds:
            result.update(
                {
                    "status": "long_running_attached",
                    "reason": "reviewer has been attached to a host for a long time; inspect before cleanup",
                }
            )
        return result

    if elapsed >= stale_seconds:
        result.update(
            {
                "status": "long_running_unknown_parent",
                "reason": "reviewer has an unexpected parent process and has been alive for a long time",
            }
        )
    else:
        result.update(
            {
                "status": "unknown_parent",
                "reason": "reviewer is attached to an unexpected parent process",
            }
        )
    return result


def _extension_installations(home: Path) -> list[dict[str, Any]]:
    installations: list[dict[str, Any]] = []
    for root_name in (".vscode/extensions", ".vscode-insiders/extensions"):
        root = home / root_name
        if not root.exists():
            continue
        for candidate in root.glob("openai.chatgpt-*"):
            if not candidate.is_dir():
                continue
            try:
                stat_result = candidate.stat()
            except OSError:
                continue
            installations.append(
                {
                    "path": str(candidate),
                    "mtime": stat_result.st_mtime,
                    "mtime_readable": _format_epoch(stat_result.st_mtime),
                }
            )
    installations.sort(key=lambda item: item["mtime"], reverse=True)
    return installations


def _summarize_extension_installations(installations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(installations),
        "multiple_versions": len(installations) > 1,
        "installations": installations,
    }


def _select_latest_extension_binary(home: Path) -> Optional[str]:
    if os.name == "nt":
        roots = [home / ".vscode/extensions", home / ".vscode-insiders/extensions"]
        candidates: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            for extension in root.glob("openai.chatgpt-*"):
                if not extension.is_dir():
                    continue
                for relative in ("bin/win32-x64/codex.exe", "bin/win32-arm64/codex.exe", "bin/win32-ia32/codex.exe"):
                    candidate = extension / relative
                    if candidate.exists():
                        candidates.append(candidate)
        if not candidates:
            return None
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return str(candidates[0])

    roots = [home / ".vscode/extensions", home / ".vscode-insiders/extensions"]
    candidates = []
    for root in roots:
        if not root.exists():
            continue
        for extension in root.glob("openai.chatgpt-*"):
            if not extension.is_dir():
                continue
            for relative in ("bin/macos-aarch64/codex", "bin/linux-x64/codex", "bin/linux-arm64/codex"):
                candidate = extension / relative
                if candidate.exists():
                    candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return str(candidates[0])


def _reference_timestamp(paths: list[Path]) -> Optional[float]:
    timestamps = []
    for path in paths:
        try:
            timestamps.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(timestamps) if timestamps else None


def _doctor_report(cwd: Path) -> dict[str, Any]:
    framework_root = _framework_root()
    docs = _choose_docs(cwd, framework_root)
    config_path = _codex_config_path()
    script_path = Path(__file__).resolve()
    codex_binary = _codex_binary()
    extension_installations = _extension_installations(Path.home())
    extension_summary = _summarize_extension_installations(extension_installations)
    selected_extension_binary = _select_latest_extension_binary(Path.home())
    reference_timestamp = _reference_timestamp([config_path, script_path])

    inspection_supported, processes, inspection_error = _list_processes()
    by_pid = {process.pid: process for process in processes}
    host_processes = [_classify_host_process(process, reference_timestamp) for process in processes if _is_app_server_process(process)]
    reviewer_processes = [
        _classify_reviewer_process(process, by_pid, reference_timestamp)
        for process in processes
        if _is_reviewer_process(process)
    ]
    stale_hosts = [process for process in host_processes if process["restart_required"]]
    cleanable_reviewers = [process for process in reviewer_processes if process["cleanable"]]
    reviewer_warnings = [
        process
        for process in reviewer_processes
        if process["status"] not in {"healthy_attached", "diagnostic_child"}
    ]

    warnings: list[str] = []
    if extension_summary["multiple_versions"]:
        warnings.append("检测到多个 openai.chatgpt 扩展版本并存，宿主可能持续复用旧 app-server。")
    if stale_hosts:
        warnings.append("存在启动时间早于最新 config.toml / reviewer wrapper 的 app-server，建议完全重启 VS Code/Codex。")
    if cleanable_reviewers:
        warnings.append("发现可安全清理的 stale reviewer wrapper 进程。")
    elif reviewer_warnings:
        warnings.append("发现长时间存活或父进程异常的 reviewer 进程，建议先诊断再手动清理。")
    if not docs["chosen_agents"] or not docs["chosen_main"]:
        warnings.append("当前仓库或 framework root 的 .codex 文档不完整，reviewer 会降级运行。")
    if not inspection_supported and inspection_error:
        warnings.append(f"进程诊断不可用：{inspection_error}")

    wrapper_ok = _binary_exists(codex_binary) and framework_root.exists()
    wrapper_health = {
        "ok": wrapper_ok,
        "server_name": SERVER_NAME,
        "server_version": SERVER_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "script_path": str(script_path),
        "config_path": str(config_path),
        "framework_root": str(framework_root),
        "framework_root_exists": framework_root.exists(),
        "codex_binary": codex_binary,
        "codex_binary_exists": _binary_exists(codex_binary),
        "documents": docs,
        "tools": [tool["name"] for tool in TOOLS],
    }

    status = "error" if not wrapper_ok else "warn" if warnings else "ok"
    return {
        "status": status,
        "wrapper_health": wrapper_health,
        "extension_scan": {
            **extension_summary,
            "selected_binary": selected_extension_binary,
            "selected_binary_exists": bool(selected_extension_binary and Path(selected_extension_binary).exists()),
        },
        "host_diagnostics": {
            "inspection_supported": inspection_supported,
            "inspection_error": inspection_error,
            "reference_updated_at": _format_epoch(reference_timestamp),
            "restart_required": bool(stale_hosts),
            "stale_hosts": stale_hosts,
            "active_hosts": host_processes,
        },
        "reviewer_processes": {
            "inspection_supported": inspection_supported,
            "stale": cleanable_reviewers,
            "warnings": reviewer_warnings,
            "all": reviewer_processes,
            "cleanable_pids": [process["pid"] for process in cleanable_reviewers],
        },
        "warnings": warnings,
    }


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name == "nt":
        success, _, _ = _run_process_command(["taskkill", "/PID", str(pid), "/T", "/F"])
        return success

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    for _ in range(20):
        if not _pid_exists(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return not _pid_exists(pid)
    return not _pid_exists(pid)


def _cleanup_report(scope: str) -> dict[str, Any]:
    if scope != DEFAULT_CLEANUP_SCOPE:
        return {
            "status": "warn",
            "scope": scope,
            "killed_pids": [],
            "failed_pids": [],
            "skipped": [f"unsupported cleanup scope: {scope}"],
        }

    report = _doctor_report(Path.cwd())
    targets = report["reviewer_processes"]["cleanable_pids"]
    killed_pids: list[int] = []
    failed_pids: list[int] = []
    for pid in targets:
        if _terminate_pid(pid):
            killed_pids.append(pid)
        else:
            failed_pids.append(pid)

    status = "ok" if not failed_pids else "warn"
    if not targets:
        status = "ok"
    return {
        "status": status,
        "scope": scope,
        "killed_pids": killed_pids,
        "failed_pids": failed_pids,
        "skipped": [] if targets else ["no cleanable stale reviewer processes found"],
    }


def _print_doctor_human(report: dict[str, Any]) -> None:
    print(f"Doctor status: {report['status'].upper()}")
    wrapper_health = report["wrapper_health"]
    print(f"- Wrapper health: {'ok' if wrapper_health['ok'] else 'error'}")
    print(f"- Wrapper protocol: {wrapper_health['protocol_version']}")
    print(f"- Codex binary: {wrapper_health['codex_binary']} ({'found' if wrapper_health['codex_binary_exists'] else 'missing'})")
    print(f"- Framework root: {wrapper_health['framework_root']} ({'found' if wrapper_health['framework_root_exists'] else 'missing'})")
    selected_binary = report["extension_scan"]["selected_binary"] or "not found"
    print(f"- Selected VS Code Codex binary: {selected_binary}")
    if report["warnings"]:
        print("- Warnings:")
        for warning in report["warnings"]:
            print(f"  * {warning}")
    else:
        print("- Warnings: none")
    stale_pids = report["reviewer_processes"]["cleanable_pids"]
    if stale_pids:
        print(f"- Cleanable reviewer PIDs: {', '.join(str(pid) for pid in stale_pids)}")
    print("- Next step for handshake issues: run `codex_reviewer_mcp.py probe --json`.")


def _print_review_gate_human(report: dict[str, Any]) -> None:
    print(f"Review gate: {'PASS' if report['gate_passed'] else 'BLOCKED'}")
    print(f"- Review mode: {report['review_mode']}")
    print(f"- Session status: {report['session_status']}")
    print(f"- Artifact path: {report['artifact_path']} ({'present' if report['artifact_exists'] else 'missing'})")
    if report["conversation_id"]:
        print(f"- Conversation ID: {report['conversation_id']}")
    if report["blocking_reason"]:
        print(f"- Blocking reason: {report['blocking_reason']}")


def _print_review_status_human(report: dict[str, Any]) -> None:
    print(f"Review status: {report['status']}")
    print(f"- Job ID: {report['job_id']}")
    print(f"- Tool: {report['tool_name']}")
    if report["task_marker"]:
        print(f"- Task marker: {report['task_marker']}")
    if report["conversation_id"]:
        print(f"- Conversation ID: {report['conversation_id']}")
    print(f"- Artifact exists: {report['artifact_exists']}")


def _print_cleanup_human(report: dict[str, Any]) -> None:
    print(f"Cleanup status: {report['status'].upper()}")
    print(f"- Scope: {report['scope']}")
    if report["killed_pids"]:
        print(f"- Killed reviewer PIDs: {', '.join(str(pid) for pid in report['killed_pids'])}")
    if report["failed_pids"]:
        print(f"- Failed reviewer PIDs: {', '.join(str(pid) for pid in report['failed_pids'])}")
    if report["skipped"]:
        print(f"- Notes: {'; '.join(report['skipped'])}")


def _read_message_with_timeout(
    stream: Any,
    timeout_seconds: float,
    reader: Callable[..., Optional[Dict[str, Any]]],
) -> Dict[str, Any]:
    box: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["result"] = reader(stream, allow_eof=False)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TimeoutError(f"timed out waiting for MCP message after {timeout_seconds}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _probe_transport(transport_mode: str, timeout_seconds: int, script_path: Path) -> dict[str, Any]:
    process = subprocess.Popen(
        [sys.executable, str(script_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    steps: list[dict[str, Any]] = []
    stderr_text = ""
    status = "ok"
    error: Optional[str] = None
    tool_names: list[str] = []
    initialize_response_protocol: Optional[str] = None
    write_message: Callable[[Any, Dict[str, Any]], None] = _write_jsonl_message
    read_message: Callable[..., Optional[Dict[str, Any]]] = _read_jsonl_message
    if transport_mode == TRANSPORT_CONTENT_LENGTH:
        write_message = _write_content_length_message
        read_message = _read_content_length_message

    try:
        assert process.stdin is not None
        assert process.stdout is not None

        initialize_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"elicitation": {}, "roots": {}, "sampling": {}},
                "clientInfo": {"name": f"probe-{transport_mode}", "version": SERVER_VERSION},
            },
        }

        started = time.perf_counter()
        write_message(process.stdin, initialize_payload)
        initialize_response = _read_message_with_timeout(process.stdout, timeout_seconds, read_message)
        initialize_elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        initialize_response_protocol = initialize_response.get("result", {}).get("protocolVersion")
        capabilities = initialize_response.get("result", {}).get("capabilities", {})
        initialize_ok = (
            initialize_response_protocol == PROTOCOL_VERSION
            and capabilities.get("tools", {}).get("listChanged") is False
        )
        steps.append(
            {
                "method": "initialize",
                "ok": initialize_ok,
                "elapsed_ms": initialize_elapsed_ms,
                "response_protocol_version": initialize_response_protocol,
                "capabilities": capabilities,
            }
        )
        if not initialize_ok:
            raise RuntimeError("initialize response did not advertise the expected protocol or tool capabilities")

        write_message(
            process.stdin,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        steps.append({"method": "notifications/initialized", "ok": True})

        started = time.perf_counter()
        write_message(process.stdin, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_response = _read_message_with_timeout(process.stdout, timeout_seconds, read_message)
        tools_elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        tool_names = [tool.get("name", "") for tool in tools_response.get("result", {}).get("tools", [])]
        required_tools = {"codex", "codex_reply", "review_gate", "review_status"}
        tools_ok = required_tools.issubset(set(tool_names))
        steps.append(
            {
                "method": "tools/list",
                "ok": tools_ok,
                "elapsed_ms": tools_elapsed_ms,
                "tool_names": tool_names,
            }
        )
        if not tools_ok:
            raise RuntimeError("tools/list response did not contain the expected reviewer tools")
    except Exception as exc:  # noqa: BLE001
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            _, stderr_output = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr_output = process.communicate(timeout=5)
        stderr_text = stderr_output.decode("utf-8", errors="replace").strip()

    return {
        "transport": transport_mode,
        "status": status,
        "steps": steps,
        "tool_names": tool_names,
        "initialize_response_protocol": initialize_response_protocol,
        "stderr": stderr_text,
        "error": error,
    }


def _probe_payload(timeout_seconds: int) -> dict[str, Any]:
    script_path = Path(__file__).resolve()
    jsonl_report = _probe_transport(TRANSPORT_JSONL, timeout_seconds, script_path)
    content_length_report = _probe_transport(TRANSPORT_CONTENT_LENGTH, timeout_seconds, script_path)

    status = "ok"
    warnings: list[str] = []
    if jsonl_report["status"] != "ok":
        status = "error"
    elif content_length_report["status"] != "ok":
        status = "warn"
        warnings.append("legacy Content-Length compatibility probe failed")

    return {
        "status": status,
        "server_name": SERVER_NAME,
        "server_script": str(script_path),
        "protocol_version": PROTOCOL_VERSION,
        "timeout_seconds": timeout_seconds,
        "primary_transport": TRANSPORT_JSONL,
        "steps": jsonl_report["steps"],
        "tool_names": jsonl_report["tool_names"],
        "initialize_response_protocol": jsonl_report["initialize_response_protocol"],
        "stderr": jsonl_report["stderr"],
        "error": jsonl_report["error"],
        "transport_reports": {
            TRANSPORT_JSONL: jsonl_report,
            TRANSPORT_CONTENT_LENGTH: content_length_report,
        },
        "warnings": warnings,
    }


def _print_probe_human(report: dict[str, Any]) -> None:
    print(f"Probe status: {report['status'].upper()}")
    print(f"- Protocol version: {report['protocol_version']}")
    print(f"- Server script: {report['server_script']}")
    print(f"- Timeout seconds: {report['timeout_seconds']}")
    print(f"- Primary transport: {report['primary_transport']}")
    for transport_name, transport_report in report.get("transport_reports", {}).items():
        print(f"- Transport {transport_name}: {transport_report['status'].upper()}")
        for step in transport_report["steps"]:
            line = f"  * {step['method']}: {'ok' if step['ok'] else 'error'}"
            if "elapsed_ms" in step:
                line += f" ({step['elapsed_ms']} ms)"
            print(line)
        if transport_report["tool_names"]:
            print(f"  * Tool names: {', '.join(transport_report['tool_names'])}")
        if transport_report["error"]:
            print(f"  * Error: {transport_report['error']}")
        if transport_report["stderr"]:
            print(f"  * Stderr: {transport_report['stderr']}")
    if report.get("warnings"):
        print(f"- Warnings: {'; '.join(report['warnings'])}")


def _handle_request(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    method = message.get("method")
    message_id = message.get("id")
    _diagnostic_log("received_method", method=method, message_id=message_id)

    if method == "initialize":
        params = message.get("params", {})
        _diagnostic_log(
            "initialize",
            requested_protocol_version=params.get("protocolVersion"),
            chosen_protocol_version=PROTOCOL_VERSION,
        )
        return _json_rpc_result(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": _server_capabilities(),
            },
        )

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return _json_rpc_result(message_id, {})

    if method == "tools/list":
        started = time.perf_counter()
        response = _json_rpc_result(message_id, {"tools": TOOLS})
        _diagnostic_log("tools_list", elapsed_ms=round((time.perf_counter() - started) * 1000, 3), tool_count=len(TOOLS))
        return response

    if method == "resources/list":
        return _json_rpc_result(message_id, {"resources": []})

    if method == "resources/templates/list":
        return _json_rpc_result(message_id, {"resourceTemplates": []})

    if method == "tools/call":
        params = message.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        _diagnostic_log("tools_call", tool_name=tool_name)
        try:
            if tool_name == "codex":
                return _json_rpc_result(message_id, _handle_codex(arguments))
            if tool_name == "codex_reply":
                return _json_rpc_result(message_id, _handle_codex_reply(arguments))
            if tool_name == "review_status":
                return _json_rpc_result(message_id, _handle_review_status(arguments))
            if tool_name == "review_gate":
                return _json_rpc_result(message_id, _handle_review_gate(arguments))
            return _json_rpc_error(message_id, -32601, f"Unknown tool: {tool_name}")
        except Exception as exc:  # noqa: BLE001
            payload = {
                "server": SERVER_NAME,
                "status": "error",
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            return _json_rpc_result(message_id, _tool_result(payload, is_error=True))

    if "id" in message:
        return _json_rpc_error(message_id, -32601, f"Unsupported method: {method}")
    return None


TOOLS = [
    {
        "name": "codex",
        "description": "Start a reviewer Codex session with a stable wrapper that captures the real conversation_id/thread_id.",
        "inputSchema": {
            "type": "object",
            "required": ["prompt"],
            "additionalProperties": True,
            "properties": {
                "prompt": {"type": "string"},
                "cwd": {"type": "string", "description": "Target repository root for the reviewer session."},
                "task_marker": {"type": "string"},
                "artifact_path": {"type": "string"},
                "framework_root": {"type": "string"},
                "model": {"type": "string"},
                "profile": {"type": "string"},
                "sandbox": {
                    "type": "string",
                    "enum": ["read-only", "workspace-write", "danger-full-access"],
                },
                "approval_policy": {"type": "string"},
                "developer_instructions": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "default": 240},
                "dry_run": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "codex_reply",
        "description": "Resume a reviewer Codex session with a known conversation_id/thread_id.",
        "inputSchema": {
            "type": "object",
            "required": ["prompt"],
            "additionalProperties": True,
            "properties": {
                "conversation_id": {"type": "string"},
                "conversationId": {"type": "string"},
                "prompt": {"type": "string"},
                "cwd": {"type": "string"},
                "artifact_path": {"type": "string"},
                "framework_root": {"type": "string"},
                "model": {"type": "string"},
                "profile": {"type": "string"},
                "sandbox": {
                    "type": "string",
                    "enum": ["read-only", "workspace-write", "danger-full-access"],
                },
                "approval_policy": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1, "default": 240},
                "dry_run": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "review_gate",
        "description": "Validate whether reviewer output is complete enough for the main Codex to finish the task.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "cwd": {"type": "string"},
                "conversation_id": {"type": "string"},
                "conversationId": {"type": "string"},
                "task_marker": {"type": "string"},
                "artifact_path": {"type": "string", "default": DEFAULT_REVIEW_ARTIFACT},
                "allow_local_fallback": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "review_status",
        "description": "Inspect the status of an asynchronous reviewer job.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "cwd": {"type": "string"},
                "job_id": {"type": "string"},
                "conversation_id": {"type": "string"},
                "conversationId": {"type": "string"},
                "task_marker": {"type": "string"},
            },
        },
    },
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="codex-reviewer wrapper")
    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser("doctor", help="Inspect wrapper health and stale reviewer processes.")
    doctor_parser.add_argument("--cwd", default=os.getcwd(), help="Target repository root for document checks.")
    doctor_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    probe_parser = subparsers.add_parser("probe", help="Run a local MCP handshake probe against this wrapper.")
    probe_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_PROBE_TIMEOUT_SECONDS,
        help="Probe timeout for each response frame.",
    )
    probe_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    gate_parser = subparsers.add_parser("review-gate", help="Validate whether review artifacts satisfy the completion gate.")
    gate_parser.add_argument("--cwd", default=os.getcwd(), help="Target repository root.")
    gate_parser.add_argument("--artifact-path", default=DEFAULT_REVIEW_ARTIFACT, help="Review artifact path relative to cwd.")
    gate_parser.add_argument("--conversation-id", default=None, help="Known conversation id.")
    gate_parser.add_argument("--task-marker", default=None, help="Known task marker.")
    gate_parser.add_argument("--allow-local-fallback", action="store_true", default=True, help="Allow local reviewer fallback when MCP reviewer is unavailable.")
    gate_parser.add_argument("--disallow-local-fallback", action="store_false", dest="allow_local_fallback", help="Require an MCP reviewer session.")
    gate_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    status_parser = subparsers.add_parser("review-status", help="Inspect the status of a reviewer job.")
    status_parser.add_argument("--cwd", default=os.getcwd(), help="Target repository root.")
    status_parser.add_argument("--job-id", default=None, help="Reviewer job id.")
    status_parser.add_argument("--conversation-id", default=None, help="Known conversation id.")
    status_parser.add_argument("--task-marker", default=None, help="Known task marker.")
    status_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    worker_parser = subparsers.add_parser("run-job", help="Run a persisted reviewer job in the background.")
    worker_parser.add_argument("--cwd", required=True, help="Target repository root.")
    worker_parser.add_argument("--job-id", required=True, help="Reviewer job id.")

    cleanup_parser = subparsers.add_parser("cleanup", help="Clean up stale reviewer wrapper processes.")
    cleanup_parser.add_argument("--scope", default=DEFAULT_CLEANUP_SCOPE, help="Cleanup scope. Only 'reviewer' is supported.")
    cleanup_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    return parser


def _run_server() -> int:
    _diagnostic_log("server_start", server_name=SERVER_NAME, protocol_version=PROTOCOL_VERSION, argv=sys.argv[1:])
    try:
        _run_job_janitor(Path(os.getcwd()).expanduser().resolve())
    except Exception as exc:  # noqa: BLE001
        _diagnostic_log("job_janitor_failed", cwd=os.getcwd(), error=str(exc), traceback=traceback.format_exc())
    while True:
        try:
            message = _read_message()
        except Exception as exc:  # noqa: BLE001
            _diagnostic_log("read_exception", error=str(exc), traceback=traceback.format_exc())
            return 1
        if message is None:
            _diagnostic_log("server_eof")
            return 0
        try:
            response = _handle_request(message)
        except Exception as exc:  # noqa: BLE001
            _diagnostic_log(
                "request_exception",
                method=message.get("method"),
                error=str(exc),
                traceback=traceback.format_exc(),
            )
            if "id" in message:
                response = _json_rpc_error(message.get("id"), -32603, "Internal server error")
            else:
                return 1
        try:
            if response is not None:
                _write_message(response)
        except Exception as exc:  # noqa: BLE001
            _diagnostic_log("write_exception", error=str(exc), traceback=traceback.format_exc())
            return 1


def _run_doctor_command(args: argparse.Namespace) -> int:
    report = _doctor_report(Path(args.cwd).expanduser().resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_doctor_human(report)
    return 1 if report["status"] == "error" else 0


def _run_probe_command(args: argparse.Namespace) -> int:
    _diagnostic_log("probe_start", timeout_seconds=args.timeout_seconds)
    report = _probe_payload(args.timeout_seconds)
    _diagnostic_log("probe_result", status=report["status"], error=report["error"], tool_names=report["tool_names"])
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_probe_human(report)
    return 0 if report["status"] in {"ok", "warn"} else 1


def _run_review_gate_command(args: argparse.Namespace) -> int:
    report = _review_gate_payload(
        cwd=Path(args.cwd).expanduser().resolve(),
        artifact_path=args.artifact_path,
        conversation_id=args.conversation_id,
        task_marker=args.task_marker,
        allow_local_fallback=args.allow_local_fallback,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_review_gate_human(report)
    return 0 if report["gate_passed"] else 2


def _run_review_status_command(args: argparse.Namespace) -> int:
    report = _review_status_payload(
        cwd=Path(args.cwd).expanduser().resolve(),
        job_id=args.job_id,
        conversation_id=args.conversation_id,
        task_marker=args.task_marker,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_review_status_human(report)
    return 0


def _run_cleanup_command(args: argparse.Namespace) -> int:
    report = _cleanup_report(args.scope)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_cleanup_human(report)
    return 0 if report["status"] in {"ok", "warn"} else 1


def main() -> int:
    if len(sys.argv) == 1:
        return _run_server()

    parser = _build_parser()
    args = parser.parse_args()
    if args.command == "doctor":
        return _run_doctor_command(args)
    if args.command == "probe":
        return _run_probe_command(args)
    if args.command == "review-gate":
        return _run_review_gate_command(args)
    if args.command == "review-status":
        return _run_review_status_command(args)
    if args.command == "run-job":
        return _run_job_worker(Path(args.cwd).expanduser().resolve(), args.job_id)
    if args.command == "cleanup":
        return _run_cleanup_command(args)
    return _run_server()


if __name__ == "__main__":
    raise SystemExit(main())
