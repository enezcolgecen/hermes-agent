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
        if os.name == "nt":
            from hermes_cli.local_runtime import key_file_windows
            monkeypatch.setattr(key_file_windows, "_open_handle", deny)
        else:
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

@pytest.mark.platforms("windows")
@pytest.mark.parametrize("condition", ["parent_symlink", "parent_junction", "swap_leaf_before", "swap_parent_before"])
def test_windows_key_file_rejects_redirects_even_during_open(tmp_path, monkeypatch, condition):
    import _winapi
    from hermes_cli.local_runtime import key_file_windows as windows

    target = tmp_path / "target"
    target.mkdir()
    secret = "fixture-credential-value-123456789"
    (target / ".api_key").write_text(secret, encoding="utf-8")
    home = tmp_path / "home"
    if condition == "parent_symlink":
        home.symlink_to(target, target_is_directory=True)
    elif condition == "parent_junction":
        _winapi.CreateJunction(str(target), str(home))
    else:
        home.mkdir()
        (home / ".api_key").write_text(secret, encoding="utf-8")
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: home)
    original = windows._open_handle

    def race(path, access, share, creation, flags):
        if condition == "swap_leaf_before" and path == home / ".api_key" and creation == 3:
            path.unlink()
            path.symlink_to(target / ".api_key")
        if condition == "swap_parent_before" and path == home:
            home.rename(tmp_path / "moved")
            _winapi.CreateJunction(str(target), str(home))
        return original(path, access, share, creation, flags)

    monkeypatch.setattr(windows, "_open_handle", race)
    with pytest.raises((OSError, RuntimeError)):
        supervisor._stable_api_key()
    assert (target / ".api_key").read_text(encoding="utf-8") == secret


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("replace", ["file", "parent"])
def test_windows_validated_handles_deny_replacement_until_io_finishes(tmp_path, monkeypatch, replace):
    import msvcrt

    home = tmp_path / "home"
    home.mkdir()
    key_path = home / ".api_key"
    secret = "fixture-credential-value-123456789"
    key_path.write_text(secret, encoding="utf-8")
    monkeypatch.setattr(supervisor, "runtimes_root", lambda: home)
    original = msvcrt.open_osfhandle
    attempted = []

    def race(handle, flags):
        # This runs AFTER native validation, before the stream reads a byte.
        attempted.append(replace)
        with pytest.raises(OSError):
            if replace == "file":
                key_path.unlink()
            else:
                home.rename(tmp_path / "moved")
        return original(handle, flags)

    monkeypatch.setattr(msvcrt, "open_osfhandle", race)
    assert supervisor._stable_api_key() == secret
    assert attempted == [replace]
    assert key_path.read_text(encoding="utf-8") == secret
    # Locks are not leaked after the operation.
    key_path.unlink()
    home.rename(tmp_path / "released")
