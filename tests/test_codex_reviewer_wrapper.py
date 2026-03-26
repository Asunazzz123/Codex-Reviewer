from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "codex_reviewer_mcp.py"

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


class TaskMarkerAndJobTests(unittest.TestCase):
    def test_normalize_task_marker_strips_brackets(self) -> None:
        self.assertEqual(
            MODULE._normalize_task_marker("[TASK_MARKER: 20260326-151553-RETEST]"),
            "20260326-151553-RETEST",
        )
        self.assertEqual(MODULE._normalize_task_marker("20260326-151553-RETEST"), "20260326-151553-RETEST")

    def test_save_session_deduplicates_normalized_task_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            MODULE._ensure_artifact_root(cwd)

            MODULE._save_session(
                cwd=cwd,
                task_marker="[TASK_MARKER: 20260326-151553-RETEST]",
                conversation_id=None,
                description="first",
                status="queued",
                artifact_paths=[".codex/context-initial.json"],
            )
            MODULE._save_session(
                cwd=cwd,
                task_marker="20260326-151553-RETEST",
                conversation_id="thread-1",
                description="second",
                status="completed",
                artifact_paths=[".codex/context-initial.json"],
            )

            sessions = MODULE._load_session_data(MODULE._session_file(cwd))["sessions"]
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["task_marker"], "20260326-151553-RETEST")
            self.assertEqual(sessions[0]["conversation_id"], "thread-1")

    def test_find_active_job_reuses_running_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            MODULE._ensure_artifact_root(cwd)
            job = MODULE._create_job_record(
                tool_name="codex",
                cwd=cwd,
                framework_root=cwd,
                task_marker="20260326-151553-RETEST",
                conversation_id=None,
                prompt="scan",
                artifact_path=".codex/context-initial.json",
                developer_instructions=None,
                model="gpt-5.4",
                profile=None,
                sandbox="workspace-write",
                approval_policy="on-request",
                timeout_seconds=240,
            )
            job["status"] = "running"
            MODULE._save_job(cwd, job)

            existing = MODULE._find_active_job(
                cwd,
                tool_name="codex",
                task_marker="[TASK_MARKER: 20260326-151553-RETEST]",
                conversation_id=None,
                artifact_path=".codex/context-initial.json",
            )
            self.assertIsNotNone(existing)
            assert existing is not None
            self.assertEqual(existing["job_id"], job["job_id"])

    def test_review_status_prefers_job_id_then_conversation_then_task_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            MODULE._ensure_artifact_root(cwd)
            first = MODULE._create_job_record(
                tool_name="codex",
                cwd=cwd,
                framework_root=cwd,
                task_marker="marker-one",
                conversation_id="thread-one",
                prompt="scan one",
                artifact_path=".codex/context-one.json",
                developer_instructions=None,
                model=None,
                profile=None,
                sandbox="workspace-write",
                approval_policy="on-request",
                timeout_seconds=240,
            )
            second = MODULE._create_job_record(
                tool_name="codex_reply",
                cwd=cwd,
                framework_root=cwd,
                task_marker="marker-two",
                conversation_id="thread-two",
                prompt="scan two",
                artifact_path=".codex/context-two.json",
                developer_instructions=None,
                model=None,
                profile=None,
                sandbox="workspace-write",
                approval_policy="on-request",
                timeout_seconds=240,
            )
            first["status"] = "completed"
            second["status"] = "completed"
            MODULE._save_job(cwd, first)
            time.sleep(0.01)
            MODULE._save_job(cwd, second)

            by_job_id = MODULE._review_status_payload(
                cwd=cwd,
                job_id=first["job_id"],
                conversation_id="thread-two",
                task_marker="marker-two",
            )
            self.assertEqual(by_job_id["job_id"], first["job_id"])

            by_conversation = MODULE._review_status_payload(
                cwd=cwd,
                job_id=None,
                conversation_id="thread-two",
                task_marker="marker-one",
            )
            self.assertEqual(by_conversation["job_id"], second["job_id"])

            by_marker = MODULE._review_status_payload(
                cwd=cwd,
                job_id=None,
                conversation_id=None,
                task_marker="[TASK_MARKER: marker-one]",
            )
            self.assertEqual(by_marker["job_id"], first["job_id"])

    def test_janitor_marks_missing_running_pid_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            MODULE._ensure_artifact_root(cwd)
            job = MODULE._create_job_record(
                tool_name="codex",
                cwd=cwd,
                framework_root=cwd,
                task_marker="marker-stale",
                conversation_id=None,
                prompt="scan stale",
                artifact_path=".codex/context.json",
                developer_instructions=None,
                model=None,
                profile=None,
                sandbox="workspace-write",
                approval_policy="on-request",
                timeout_seconds=240,
            )
            job["status"] = "running"
            job["pid"] = 999_999_999
            MODULE._save_job(cwd, job)

            report = MODULE._run_job_janitor(cwd)
            self.assertEqual(report["stale"], 1)
            updated = MODULE._load_job(cwd, job["job_id"])
            assert updated is not None
            self.assertEqual(updated["status"], "stale")

    def test_janitor_marks_old_queued_job_without_pid_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            MODULE._ensure_artifact_root(cwd)
            job = MODULE._create_job_record(
                tool_name="codex",
                cwd=cwd,
                framework_root=cwd,
                task_marker="marker-failed",
                conversation_id=None,
                prompt="scan failed",
                artifact_path=".codex/context.json",
                developer_instructions=None,
                model=None,
                profile=None,
                sandbox="workspace-write",
                approval_policy="on-request",
                timeout_seconds=240,
            )
            job["created_at_unix"] = time.time() - MODULE.JOB_QUEUE_STALE_SECONDS - 5
            job["status"] = "queued"
            MODULE._save_job(cwd, job)

            report = MODULE._run_job_janitor(cwd)
            self.assertEqual(report["failed"], 1)
            updated = MODULE._load_job(cwd, job["job_id"])
            assert updated is not None
            self.assertEqual(updated["status"], "failed")


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
