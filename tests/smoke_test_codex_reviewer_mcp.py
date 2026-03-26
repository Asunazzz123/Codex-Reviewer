#!/opt/anaconda3/bin/python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / ".scripts" / "codex_reviewer_mcp.py"
PROTOCOL_VERSION = "2025-06-18"


def send(process: subprocess.Popen[str], payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8")
    assert process.stdin is not None
    process.stdin.buffer.write(header)
    process.stdin.buffer.write(raw)
    process.stdin.flush()


def receive(process: subprocess.Popen[str]) -> dict:
    assert process.stdout is not None
    headers: dict[str, str] = {}
    while True:
        line = process.stdout.buffer.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout unexpectedly")
        if line in (b"\r\n", b"\n"):
            break
        key, value = line.decode("utf-8").split(":", 1)
        headers[key.lower()] = value.strip()

    content_length = int(headers["content-length"])
    body = process.stdout.buffer.read(content_length)
    return json.loads(body.decode("utf-8"))


def main() -> int:
    base_env = {
        **os.environ,
        "CODEX_REVIEWER_FRAMEWORK_ROOT": str(ROOT),
        "CODEX_BINARY": sys.executable,
    }

    process = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(ROOT),
        env=base_env,
    )

    try:
        send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"elicitation": {}, "roots": {}, "sampling": {}},
                    "clientInfo": {"name": "smoke", "version": "0.1.0"},
                },
            },
        )
        initialize_response = receive(process)
        assert initialize_response["result"]["serverInfo"]["name"] == "codex-reviewer-wrapper"
        assert initialize_response["result"]["protocolVersion"] == PROTOCOL_VERSION
        assert initialize_response["result"]["capabilities"]["tools"]["listChanged"] is False

        send(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        send(process, {"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}})
        resources_response = receive(process)
        assert resources_response["result"]["resources"] == []

        send(process, {"jsonrpc": "2.0", "id": 3, "method": "resources/templates/list", "params": {}})
        resource_templates_response = receive(process)
        assert resource_templates_response["result"]["resourceTemplates"] == []

        send(process, {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}})
        tools_response = receive(process)
        tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
        assert {"codex", "codex_reply", "review_gate"} <= tool_names

        send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "codex",
                    "arguments": {
                        "prompt": "[TASK_MARKER: 20260323-120000-ABCD]\n请做上下文扫描。",
                        "cwd": str(ROOT),
                        "artifact_path": ".codex/context-initial.json",
                        "dry_run": True,
                    },
                },
            },
        )
        dry_run_response = receive(process)
        payload = dry_run_response["result"]["structuredContent"]
        assert payload["status"] == "dry_run"
        assert payload["task_marker"] == "[TASK_MARKER: 20260323-120000-ABCD]"
        assert payload["artifact_path"] == ".codex/context-initial.json"
        assert payload["documents"]["chosen_agents"]
        assert payload["documents"]["chosen_main"]
        assert payload["documents"]["chosen_agents"].endswith("/.codex/AGENTS.md")
        assert payload["documents"]["chosen_main"].endswith("/.codex/CODEX.md")
        assert payload["command"][1] == "exec"

        with tempfile.TemporaryDirectory(prefix="review-gate-smoke-") as temp_dir:
            repo_root = Path(temp_dir)
            artifact_path = repo_root / ".codex" / "review-report.md"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)

            send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {
                        "name": "review_gate",
                        "arguments": {
                            "cwd": str(repo_root),
                            "artifact_path": ".codex/review-report.md",
                            "allow_local_fallback": True,
                        },
                    },
                },
            )
            blocked_response = receive(process)["result"]["structuredContent"]
            assert blocked_response["gate_passed"] is False
            assert blocked_response["blocking_reason"] == "review_artifact_missing"

            artifact_path.write_text("# review\n", encoding="utf-8")
            send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "review_gate",
                        "arguments": {
                            "cwd": str(repo_root),
                            "artifact_path": ".codex/review-report.md",
                            "allow_local_fallback": True,
                        },
                    },
                },
            )
            passed_response = receive(process)["result"]["structuredContent"]
            assert passed_response["gate_passed"] is True
            assert passed_response["review_mode"] == "local_fallback"
            assert passed_response["artifact_exists"] is True

        doctor_result = subprocess.run(
            [sys.executable, str(SERVER), "doctor", "--cwd", str(ROOT), "--json"],
            check=False,
            capture_output=True,
            text=True,
            env=base_env,
        )
        assert doctor_result.returncode in {0, 1}
        doctor_payload = json.loads(doctor_result.stdout)
        assert doctor_payload["status"] in {"ok", "warn"}
        assert doctor_payload["wrapper_health"]["server_name"] == "codex-reviewer-wrapper"
        assert doctor_payload["wrapper_health"]["protocol_version"] == PROTOCOL_VERSION
        assert "review_gate" in doctor_payload["wrapper_health"]["tools"]

        probe_result = subprocess.run(
            [sys.executable, str(SERVER), "probe", "--json"],
            check=False,
            capture_output=True,
            text=True,
            env=base_env,
        )
        assert probe_result.returncode == 0
        probe_payload = json.loads(probe_result.stdout)
        assert probe_payload["status"] == "ok"
        assert probe_payload["protocol_version"] == PROTOCOL_VERSION
        assert probe_payload["initialize_response_protocol"] == PROTOCOL_VERSION
        assert {"codex", "codex_reply", "review_gate"} <= set(probe_payload["tool_names"])

        with tempfile.TemporaryDirectory(prefix="reviewer-log-smoke-") as temp_dir:
            log_path = Path(temp_dir) / "codex-reviewer.log"
            logged_env = {**base_env, "CODEX_REVIEWER_LOG_PATH": str(log_path)}
            logged_probe = subprocess.run(
                [sys.executable, str(SERVER), "probe", "--json"],
                check=False,
                capture_output=True,
                text=True,
                env=logged_env,
            )
            assert logged_probe.returncode == 0
            log_text = log_path.read_text(encoding="utf-8")
            assert "received_method" in log_text
            assert "initialize" in log_text
            assert "tools_list" in log_text

        with tempfile.TemporaryDirectory(prefix="review-gate-cli-") as temp_dir:
            repo_root = Path(temp_dir)
            report_path = repo_root / ".codex" / "review-report.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)

            failed_gate = subprocess.run(
                [
                    sys.executable,
                    str(SERVER),
                    "review-gate",
                    "--cwd",
                    str(repo_root),
                    "--artifact-path",
                    ".codex/review-report.md",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=base_env,
            )
            assert failed_gate.returncode == 2

            report_path.write_text("# review\n", encoding="utf-8")
            passed_gate = subprocess.run(
                [
                    sys.executable,
                    str(SERVER),
                    "review-gate",
                    "--cwd",
                    str(repo_root),
                    "--artifact-path",
                    ".codex/review-report.md",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=base_env,
            )
            assert passed_gate.returncode == 0
            gate_payload = json.loads(passed_gate.stdout)
            assert gate_payload["gate_passed"] is True
            assert gate_payload["review_mode"] == "local_fallback"

        print("smoke test passed")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
