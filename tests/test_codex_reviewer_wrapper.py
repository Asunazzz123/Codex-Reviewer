from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / ".scripts" / "codex_reviewer_mcp.py"

SPEC = importlib.util.spec_from_file_location("codex_reviewer_mcp", SERVER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ReviewerProcessClassificationTests(unittest.TestCase):
    def _process(
        self,
        *,
        pid: int,
        ppid: int,
        command: str,
        elapsed_seconds: int | None,
        started_at: float | None,
    ) -> object:
        return MODULE.ProcessInfo(
            pid=pid,
            ppid=ppid,
            command=command,
            elapsed_seconds=elapsed_seconds,
            started_at=started_at,
        )

    def test_healthy_attached_reviewer(self) -> None:
        host = self._process(
            pid=100,
            ppid=1,
            command="/Users/asuna/.vscode/extensions/openai.chatgpt/bin/codex app-server --analytics-default-enabled",
            elapsed_seconds=120,
            started_at=2_000.0,
        )
        reviewer = self._process(
            pid=101,
            ppid=100,
            command="/opt/anaconda3/bin/python3 /Users/asuna/.codex/scripts/codex_reviewer_mcp.py",
            elapsed_seconds=45,
            started_at=2_030.0,
        )
        result = MODULE._classify_reviewer_process(reviewer, {100: host, 101: reviewer}, 1_500.0)
        self.assertEqual(result["status"], "healthy_attached")
        self.assertFalse(result["cleanable"])

    def test_stale_orphan_reviewer_is_cleanable(self) -> None:
        reviewer = self._process(
            pid=201,
            ppid=1,
            command="/opt/anaconda3/bin/python3 /Users/asuna/.codex/scripts/codex_reviewer_mcp.py",
            elapsed_seconds=MODULE.REVIEWER_STALE_SECONDS + 1,
            started_at=1_000.0,
        )
        result = MODULE._classify_reviewer_process(reviewer, {201: reviewer}, 2_000.0)
        self.assertEqual(result["status"], "stale_orphan")
        self.assertTrue(result["cleanable"])

    def test_long_running_attached_reviewer_is_not_auto_cleaned(self) -> None:
        host = self._process(
            pid=300,
            ppid=1,
            command="/Users/asuna/.vscode/extensions/openai.chatgpt/bin/codex app-server --analytics-default-enabled",
            elapsed_seconds=2_000,
            started_at=4_000.0,
        )
        reviewer = self._process(
            pid=301,
            ppid=300,
            command="/opt/anaconda3/bin/python3 /Users/asuna/.codex/scripts/codex_reviewer_mcp.py",
            elapsed_seconds=MODULE.REVIEWER_STALE_SECONDS + 10,
            started_at=4_100.0,
        )
        result = MODULE._classify_reviewer_process(reviewer, {300: host, 301: reviewer}, 3_000.0)
        self.assertEqual(result["status"], "long_running_attached")
        self.assertFalse(result["cleanable"])

    def test_reviewer_attached_to_outdated_host_requires_restart(self) -> None:
        host = self._process(
            pid=400,
            ppid=1,
            command="/Users/asuna/.vscode/extensions/openai.chatgpt/bin/codex app-server --analytics-default-enabled",
            elapsed_seconds=600,
            started_at=1_000.0,
        )
        reviewer = self._process(
            pid=401,
            ppid=400,
            command="/opt/anaconda3/bin/python3 /Users/asuna/.codex/scripts/codex_reviewer_mcp.py",
            elapsed_seconds=120,
            started_at=1_200.0,
        )
        result = MODULE._classify_reviewer_process(reviewer, {400: host, 401: reviewer}, 2_000.0)
        self.assertEqual(result["status"], "attached_to_outdated_host")
        self.assertFalse(result["cleanable"])

    def test_host_process_restart_detection(self) -> None:
        host = self._process(
            pid=500,
            ppid=1,
            command="/Users/asuna/.vscode/extensions/openai.chatgpt/bin/codex app-server --analytics-default-enabled",
            elapsed_seconds=600,
            started_at=1_000.0,
        )
        result = MODULE._classify_host_process(host, 2_000.0)
        self.assertTrue(result["restart_required"])

    def test_extension_summary_marks_multiple_versions(self) -> None:
        summary = MODULE._summarize_extension_installations(
            [
                {"path": "/tmp/openai.chatgpt-1", "mtime": 2.0, "mtime_readable": "2026-03-25 10:00:00"},
                {"path": "/tmp/openai.chatgpt-2", "mtime": 1.0, "mtime_readable": "2026-03-24 10:00:00"},
            ]
        )
        self.assertTrue(summary["multiple_versions"])
        self.assertEqual(summary["count"], 2)


class WrapperProtocolTests(unittest.TestCase):
    def test_initialize_response_uses_current_protocol(self) -> None:
        response = MODULE._handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": MODULE.PROTOCOL_VERSION},
            }
        )
        assert response is not None
        self.assertEqual(response["result"]["protocolVersion"], MODULE.PROTOCOL_VERSION)
        self.assertEqual(response["result"]["capabilities"], {"tools": {"listChanged": False}})


class CommandBuilderTests(unittest.TestCase):
    def test_build_resume_command_avoids_exec_only_flags(self) -> None:
        command = MODULE._build_resume_command(
            codex_binary="/tmp/codex",
            conversation_id="session-123",
            prompt="continue review",
            output_path=Path("/tmp/last-message.txt"),
            cwd=Path("/tmp/worktree"),
            model="gpt-5.4",
            profile="reviewer",
            sandbox="workspace-write",
            approval_policy="on-request",
        )

        self.assertEqual(
            command,
            [
                "/tmp/codex",
                "exec",
                "resume",
                "--json",
                "--skip-git-repo-check",
                "-o",
                "/tmp/last-message.txt",
                "--full-auto",
                "--model",
                "gpt-5.4",
                "session-123",
                "continue review",
            ],
        )
        self.assertNotIn("-C", command)
        self.assertNotIn("--profile", command)


if __name__ == "__main__":
    unittest.main()
