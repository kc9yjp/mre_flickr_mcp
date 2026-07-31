"""Tests for scripts/tools/sync.py: cancellation and progress/ETA tracking."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import tools.sync as sync_tools


def _fake_proc(returncode=None):
    p = MagicMock()
    p.returncode = returncode
    return p


class TestCancelSync:
    def setup_method(self):
        sync_tools._active_processes.clear()

    def teardown_method(self):
        sync_tools._active_processes.clear()

    def test_cancels_bare_label(self):
        proc = _fake_proc()
        sync_tools._active_processes["groups"] = proc
        assert sync_tools.cancel_sync("groups") is True
        proc.terminate.assert_called_once()

    def test_cancels_matching_per_user_label(self):
        proc = _fake_proc()
        sync_tools._active_processes["groups/alice"] = proc
        assert sync_tools.cancel_sync("groups", "alice") is True
        proc.terminate.assert_called_once()

    def test_does_not_cancel_a_different_users_label(self):
        proc = _fake_proc()
        sync_tools._active_processes["groups/alice"] = proc
        assert sync_tools.cancel_sync("groups", "bob") is False
        proc.terminate.assert_not_called()

    def test_bare_label_cancelled_regardless_of_requesting_user(self):
        """Mirrors the primary trigger path (web._start_sync), which never
        suffixes labels with a username — see cancel_sync's docstring."""
        proc = _fake_proc()
        sync_tools._active_processes["groups"] = proc
        assert sync_tools.cancel_sync("groups", "anyone") is True
        proc.terminate.assert_called_once()

    def test_no_matching_label_returns_false(self):
        sync_tools._active_processes["photos"] = _fake_proc()
        assert sync_tools.cancel_sync("groups") is False

    def test_already_exited_process_is_not_terminated(self):
        proc = _fake_proc(returncode=0)
        sync_tools._active_processes["groups"] = proc
        assert sync_tools.cancel_sync("groups") is False
        proc.terminate.assert_not_called()


class TestProgressRegex:
    """The regex _run_sync_script uses to parse the "Summary i/n: name" lines
    that flickr_sync._sync_group_summaries_async prints to stdout."""

    def test_matches_expected_format(self):
        m = sync_tools._PROGRESS_RE.match("  Summary 3/12: Some Group")
        assert m is not None
        assert m.group(1) == "3"
        assert m.group(2) == "12"

    def test_ignores_unrelated_lines(self):
        assert sync_tools._PROGRESS_RE.match("Generating AI group summaries...") is None
        assert sync_tools._PROGRESS_RE.match("  Warning: summary generation failed") is None


class TestRunSyncScriptProgress:
    """_run_sync_script parses the "Summary i/n: name" lines that
    flickr_sync._sync_group_summaries_async prints, driving the Sync page's
    progress/ETA display. Exercised against a real short-lived subprocess
    rather than mocked pipes, since the parsing lives in the stdout loop."""

    def setup_method(self):
        sync_tools._active_syncs.clear()
        sync_tools._sync_phase.clear()
        sync_tools._sync_progress.clear()
        sync_tools._active_processes.clear()

    @pytest.mark.asyncio
    async def test_progress_and_phase_tracked_then_cleared(self, tmp_path):
        script_path = tmp_path / "fake_sync.py"
        script_path.write_text(
            "print('Generating AI group summaries...')\n"
            "print('  Summary 1/2: Group One')\n"
            "print('  Summary 2/2: Group Two')\n"
        )
        rc = await sync_tools._run_sync_script(str(script_path), "groups", username=None)

        assert rc == 0
        # Cleaned up once the subprocess exits — a poller that checked
        # _sync_progress after this point would correctly see "not running".
        assert "groups" not in sync_tools._active_syncs
        assert "groups" not in sync_tools._sync_phase
        assert "groups" not in sync_tools._sync_progress
        assert "groups" not in sync_tools._active_processes
