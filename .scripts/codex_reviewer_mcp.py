#!/opt/anaconda3/bin/python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any


SERVER_NAME = "codex-reviewer-wrapper"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"
TASK_MARKER_PATTERN = re.compile(r"^\[TASK_MARKER:\s*([^\]]+)\]\s*$")


def _home_path(*parts: str) -> Path:
    return Path.home().joinpath(*parts)


def _framework_root() -> Path:
    env_root = os.environ.get("CODEX_REVIEWER_FRAMEWORK_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _codex_binary() -> str:
    return os.environ.get("CODEX_BINARY", str(_home_path(".codex", "bin", "codex-latest")))


def _now_shanghai() -> str:
    # No external dependency needed; UTC+8 is sufficient for this workflow.
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 8 * 3600))


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        name, value = line.decode("utf-8").split(":", 1)
        headers[name.lower()] = value.strip()

    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None
    body = sys.stdin.buffer.read(content_length)
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


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


def _extract_task_marker(prompt: str) -> tuple[str | None, str]:
    lines = prompt.splitlines()
    if not lines:
        return None, prompt
    match = TASK_MARKER_PATTERN.match(lines[0].strip())
    if not match:
        return None, prompt
    remaining = "\n".join(lines[1:]).strip()
    return match.group(0), remaining


def _choose_docs(cwd: Path, framework_root: Path) -> dict[str, str | None]:
    local_agents = cwd / "AGENTS.md"
    local_codex = cwd / "CODEX.md"
    framework_agents = framework_root / "AGENTS.md"
    framework_codex = framework_root / "CODEX.md"

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
    artifact_path: str | None,
    developer_instructions: str | None,
    task_marker: str | None,
) -> tuple[str, dict[str, str | None]]:
    docs = _choose_docs(cwd, framework_root)
    _, prompt_body = _extract_task_marker(prompt)
    local_artifact_root = cwd / ".codex"

    lines = []
    if task_marker:
        lines.append(task_marker)
    lines.append("$codex-reviewer-workflow")
    lines.append("你是 multi-codex 架构中的审查 Codex。")
    lines.append("会话与续聊由 MCP wrapper 管理：不要猜测、编造或手工回填 conversation_id。")
    lines.append(f"当前目标仓库：{cwd}")
    lines.append(f"审查产物目录：{local_artifact_root}")
    if artifact_path:
        lines.append(f"本轮优先产物路径：{artifact_path}")
    lines.append("文档读取顺序：")
    if docs["chosen_agents"]:
        lines.append(f"1. 先读取 AGENTS.md：{docs['chosen_agents']}")
    else:
        lines.append("1. 未找到 AGENTS.md；需要在输出中声明降级。")
    if docs["chosen_main"]:
        lines.append(f"2. 再读取主文档：{docs['chosen_main']}")
    else:
        lines.append("2. 未找到 CODEX.md；需要在输出中声明降级。")
    lines.append("如果项目内缺少这些文档，可以读取上面的框架文档作为降级方案，但必须明确说明。")
    lines.append("只允许把 reviewer 产物写入项目本地 .codex/ 目录，不直接修改业务代码，除非显式覆盖。")
    if developer_instructions:
        lines.append("补充开发者约束：")
        lines.append(developer_instructions.strip())
    lines.append("以下是主 Codex 传入的任务：")
    lines.append(prompt_body.strip() or prompt.strip())
    return "\n".join(lines).strip(), docs


def _extract_thread_id_from_events(output: str) -> str | None:
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


def _find_nested_string(node: Any, key: str) -> str | None:
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


def _lookup_thread_id_from_state(cwd: Path, start_time: float) -> str | None:
    database_path = _home_path(".codex", "state_5.sqlite")
    if not database_path.exists():
        return None
    connection: sqlite3.Connection | None = None
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
    return cwd / ".codex" / "codex-reviewer-sessions.json"


def _ensure_artifact_root(cwd: Path) -> Path:
    artifact_root = cwd / ".codex"
    artifact_root.mkdir(parents=True, exist_ok=True)
    return artifact_root


def _load_session_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"updated_at": _now_shanghai(), "sessions": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"updated_at": _now_shanghai(), "sessions": []}


def _save_session(
    *,
    cwd: Path,
    task_marker: str | None,
    conversation_id: str | None,
    description: str,
    status: str,
    artifact_paths: list[str],
) -> None:
    session_path = _session_file(cwd)
    data = _load_session_data(session_path)
    sessions = data.setdefault("sessions", [])
    existing = None
    for session in sessions:
        if session.get("task_marker") == task_marker and task_marker:
            existing = session
            break
        if session.get("conversation_id") == conversation_id and conversation_id:
            existing = session
            break

    if existing is None:
        existing = {
            "task_marker": task_marker,
            "conversation_id": conversation_id,
            "created_at": _now_shanghai(),
        }
        sessions.append(existing)

    existing.update(
        {
            "conversation_id": conversation_id,
            "updated_at": _now_shanghai(),
            "cwd": str(cwd),
            "description": description[:280],
            "status": status,
            "artifact_paths": artifact_paths,
        }
    )
    data["updated_at"] = _now_shanghai()
    session_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _approval_flags(approval_policy: str | None, sandbox: str | None) -> list[str]:
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
) -> tuple[int | None, str, str, bool]:
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
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return process.returncode, stdout, stderr, timed_out
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
    model: str | None,
    profile: str | None,
    sandbox: str | None,
    approval_policy: str | None,
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
    model: str | None,
    profile: str | None,
    sandbox: str | None,
    approval_policy: str | None,
) -> list[str]:
    cmd = [codex_binary, "exec", "resume", conversation_id, "--json", "--skip-git-repo-check", "-o", str(output_path)]
    cmd.extend(_approval_flags(approval_policy, sandbox))
    if model:
        cmd.extend(["--model", model])
    if profile:
        cmd.extend(["--profile", profile])
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
    docs: dict[str, str | None],
    task_marker: str | None,
    artifact_path: str | None,
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


def _handle_codex(arguments: dict[str, Any]) -> dict[str, Any]:
    args = _normalize_args(arguments)
    prompt = str(args["prompt"])
    cwd = Path(args.get("cwd") or os.getcwd()).expanduser().resolve()
    framework_root = Path(args.get("framework_root") or _framework_root()).expanduser().resolve()
    task_marker, _ = _extract_task_marker(prompt)
    task_marker = args.get("task_marker") or task_marker
    artifact_path = args.get("artifact_path")
    developer_instructions = args.get("developer_instructions")
    timeout_seconds = int(args.get("timeout_seconds") or 300)
    model = args.get("model")
    profile = args.get("profile")
    sandbox = args.get("sandbox") or "workspace-write"
    approval_policy = args.get("approval_policy") or "on-request"
    dry_run = bool(args.get("dry_run"))

    _ensure_artifact_root(cwd)
    wrapped_prompt, docs = _build_prompt(
        prompt=prompt,
        cwd=cwd,
        framework_root=framework_root,
        artifact_path=artifact_path,
        developer_instructions=developer_instructions,
        task_marker=task_marker,
    )

    with tempfile.TemporaryDirectory(prefix="codex-reviewer-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.txt"
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
        payload = _base_payload(
            cmd=cmd,
            cwd=cwd,
            docs=docs,
            task_marker=task_marker,
            artifact_path=artifact_path,
            timeout_seconds=timeout_seconds,
        )
        if dry_run:
            payload.update({"status": "dry_run", "conversation_id": None, "assistant_message": ""})
            return _tool_result(payload)

        start_time = time.time()
        returncode, stdout, stderr, timed_out = _run_codex_command(cmd=cmd, cwd=cwd, timeout_seconds=timeout_seconds)
        conversation_id = _extract_thread_id_from_events(stdout) or _lookup_thread_id_from_state(cwd, start_time)
        assistant_message = _last_message(output_path)
        status = "timeout" if timed_out else "completed" if returncode == 0 else "error"
        artifact_paths = [path for path in [artifact_path] if path]

        _save_session(
            cwd=cwd,
            task_marker=task_marker,
            conversation_id=conversation_id,
            description=prompt,
            status=status,
            artifact_paths=artifact_paths,
        )

        payload.update(
            {
                "status": status,
                "conversation_id": conversation_id,
                "assistant_message": assistant_message,
                "stdout": stdout[-12000:],
                "stderr": stderr[-12000:],
                "returncode": returncode,
                "timed_out": timed_out,
                "artifact_paths": artifact_paths,
            }
        )
        return _tool_result(payload, is_error=status == "error")


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
    timeout_seconds = int(args.get("timeout_seconds") or 300)
    model = args.get("model")
    profile = args.get("profile")
    sandbox = args.get("sandbox") or "workspace-write"
    approval_policy = args.get("approval_policy") or "on-request"
    dry_run = bool(args.get("dry_run"))

    _ensure_artifact_root(cwd)
    docs = _choose_docs(cwd, framework_root)

    with tempfile.TemporaryDirectory(prefix="codex-reviewer-reply-") as temp_dir:
        output_path = Path(temp_dir) / "last-message.txt"
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
            task_marker=task_marker,
            artifact_path=artifact_path,
            timeout_seconds=timeout_seconds,
        )
        payload["conversation_id"] = conversation_id
        if dry_run:
            payload.update({"status": "dry_run", "assistant_message": ""})
            return _tool_result(payload)

        returncode, stdout, stderr, timed_out = _run_codex_command(cmd=cmd, cwd=cwd, timeout_seconds=timeout_seconds)
        assistant_message = _last_message(output_path)
        status = "timeout" if timed_out else "completed" if returncode == 0 else "error"
        artifact_paths = [path for path in [artifact_path] if path]

        _save_session(
            cwd=cwd,
            task_marker=task_marker,
            conversation_id=str(conversation_id),
            description=prompt,
            status=status,
            artifact_paths=artifact_paths,
        )

        payload.update(
            {
                "status": status,
                "assistant_message": assistant_message,
                "stdout": stdout[-12000:],
                "stderr": stderr[-12000:],
                "returncode": returncode,
                "timed_out": timed_out,
                "artifact_paths": artifact_paths,
            }
        )
        return _tool_result(payload, is_error=status == "error")


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
                "timeout_seconds": {"type": "integer", "minimum": 1, "default": 300},
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
                "timeout_seconds": {"type": "integer", "minimum": 1, "default": 300},
                "dry_run": {"type": "boolean", "default": False},
            },
        },
    },
]


def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")

    if method == "initialize":
        return _json_rpc_result(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
            },
        )

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return _json_rpc_result(message_id, {})

    if method == "tools/list":
        return _json_rpc_result(message_id, {"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        try:
            if tool_name == "codex":
                return _json_rpc_result(message_id, _handle_codex(arguments))
            if tool_name == "codex_reply":
                return _json_rpc_result(message_id, _handle_codex_reply(arguments))
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


def main() -> int:
    while True:
        message = _read_message()
        if message is None:
            return 0
        response = _handle_request(message)
        if response is not None:
            _write_message(response)


if __name__ == "__main__":
    raise SystemExit(main())
