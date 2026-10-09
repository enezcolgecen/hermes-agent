"""Managed local authentication stays private and fails closed without inference."""
from types import SimpleNamespace
import os
import stat

import pytest
from hermes_cli.local_runtime import supervisor


@pytest.mark.parametrize("condition", ["new", "existing", "bom", "invalid", "bom_invalid", "symlink", "unwritable"])
def test_private_persistent_key_or_closed_startup(tmp_path, monkeypatch, condition):
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: tmp_path)
    path = tmp_path / ".api_key"
    original = "test-local-credential-value-123456789"
    if condition in ("existing", "bom"):
        path.write_text(original, encoding="utf-8-sig" if condition == "bom" else "utf-8")
        path.chmod(0o644)
    elif condition in ("invalid", "bom_invalid"):
        path.write_text("short", encoding="utf-8-sig" if condition == "bom_invalid" else "utf-8")
    elif condition == "symlink":
        target = tmp_path / "unrelated"
        target.write_text(original)
        path.symlink_to(target)
    elif condition == "unwritable":
        def deny(*args, **kwargs):
            raise PermissionError("key file denied")
        monkeypatch.setattr(supervisor.os, "open", deny)
    if condition in ("invalid", "bom_invalid", "symlink", "unwritable"):
        with pytest.raises((OSError, RuntimeError)):
            supervisor._stable_api_key()
        if condition == "symlink":
            assert target.read_text() == original
        return
    key = supervisor._stable_api_key()
    assert key == path.read_text(encoding="utf-8-sig") == supervisor._stable_api_key()
    if condition in ("existing", "bom"):
        assert key == original
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("condition", ["normal", "rotated", "override", "inline_override", "embedded"])
def test_launch_uses_file_and_redacts_diagnostics_or_refuses(tmp_path, monkeypatch, condition):
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(supervisor, "_direct_io_args", lambda exe: ())
    commands = []
    monkeypatch.setattr(supervisor, "spawn_server", lambda cmd, **kwargs: (
        commands.append(cmd) or SimpleNamespace(pid=123, poll=lambda: 0), None))
    sup = supervisor.LlamaServerSupervisor(tmp_path / "llama-server", tmp_path / "models", port=19001)
    monkeypatch.setattr(sup, "_write_state", lambda: None)
    if condition == "rotated":
        supervisor.api_key_path().write_text("a-different-valid-credential-123456789")
    elif condition == "override":
        sup.extra_args = ["--api-key", "another-secret-credential-123456789"]
    elif condition == "inline_override":
        sup.extra_args = ["--api-key-file=unowned-file"]
    elif condition == "embedded":
        sup.extra_args = ["embedded=" + sup.api_key]
    if condition != "normal":
        with pytest.raises(RuntimeError):
            sup._spawn()
        assert not commands
        return
    try:
        sup._spawn()
        cmd = commands[0]
        assert "--api-key" not in cmd and all(sup.api_key not in arg for arg in cmd)
        assert cmd[cmd.index("--api-key-file") + 1] == str(supervisor.api_key_path())
        assert sup.api_key not in sup.log_path.read_text()
        assert sup.api_key not in str(supervisor._redacted_command(["embedded=" + sup.api_key], sup.api_key))
    finally:
        sup.stop()
