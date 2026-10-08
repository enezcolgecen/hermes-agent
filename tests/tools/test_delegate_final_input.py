"""Observe the final delegated input without constructing/spawning a model child."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import plugins
from tools import subagent_worktree
from tools.delegate_tool_child_run import _ChildRun
from tools.terminal_tool import get_session_cwd, record_session_cwd


def _git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True, encoding="utf-8", errors="replace",
    ).strip()


@pytest.mark.parametrize("isolation", ["off", "clean", "dirty"])
def test_final_input_observation_follows_workspace_seeding(tmp_path, monkeypatch, isolation):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(
        "delegation:\n  worktree_isolation: " + ("false" if isolation == "off" else "true") +
        "\nterminal:\n  backend: local\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.com")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    (repo / "source.txt").write_text("parent source\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "source.txt")
    _git(repo, "commit", "-m", "fixture")
    head = _git(repo, "rev-parse", "HEAD")
    parent = SimpleNamespace(session_id="parent-fixture", _current_task_id="parent-task",
                             _current_turn_id="turn-fixture", _subagent_id=None)
    record_session_cwd(parent._current_task_id, str(repo))
    seen = []
    def observe(**payload):
        seen.append(("observed", payload))
    manager = plugins.PluginManager()
    manager._hooks["subagent_start"] = [observe]
    monkeypatch.setattr(plugins, "_plugin_manager", manager)

    class InputSink:
        session_id = "input-sink-fixture"
        _delegate_role = "leaf"
        _delegate_images = []
        def run_conversation(self, user_message, task_id, **kwargs):
            seen.append(("delivered", user_message))
            assert get_session_cwd(task_id) == (run.worktree_info["path"] if run.worktree_info else str(repo))
            if isolation == "dirty":
                info = run.worktree_info
                assert info is not None
                (Path(info["path"]) / "child-only.txt").write_text("fixture edit\n", encoding="utf-8")
            return {"final_response": "fixture", "completed": True}

    requested = "Review scoped evidence without provider execution."
    run = _ChildRun(InputSink(), parent, 0, requested, "fixture-child", None)
    run.seed_workspace()
    assert seen == []  # no pre-seed/constructor observation
    result, failure, deferred = run.await_child()
    assert result is not None
    assert failure is None and not deferred and result["completed"]
    assert [stage for stage, value in seen] == ["observed", "delivered"]
    observed, delivered = seen[0][1], seen[1][1]
    assert observed["child_goal"] == delivered
    assert observed["requested_child_goal"] == requested
    assert observed["child_input_is_text"] is True
    if isolation == "off":
        assert observed["worktree_context"] is None and delivered == requested
    else:
        info = run.worktree_info
        assert info is not None and observed["worktree_context"] == info
        assert delivered == requested + subagent_worktree.build_worktree_context_note(info)
        assert info["path"] != str(repo) and _git(info["path"], "branch", "--show-current") == info["branch"]
        payload = run.attach_worktree({})["worktree"]
        assert payload["dirty"] is (isolation == "dirty")
        assert payload["pruned"] is (isolation == "clean")
        if isolation == "dirty":
            assert (Path(info["path"]) / "child-only.txt").is_file()
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""
    assert get_session_cwd(parent._current_task_id) == str(repo)
