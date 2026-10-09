"""Actual stacked PR ancestry is the attribution boundary; unknown new authors stay red."""
import json
import subprocess
from pathlib import Path

import pytest
from scripts import audit_pr_attribution as audit


def _git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                   encoding="utf-8", errors="replace").strip()


@pytest.mark.parametrize("base_source", ["explicit", "environment", "pr_metadata"])
@pytest.mark.parametrize("mapped", [True, False])
def test_stacked_pr_audits_only_own_authors_and_preserves_mapping_failure(tmp_path, monkeypatch, base_source, mapped):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "inherited@example.com")
    _git(tmp_path, "commit", "--allow-empty", "-m", "main")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", _git(tmp_path, "rev-parse", "HEAD"))
    _git(tmp_path, "checkout", "-b", "parent")
    _git(tmp_path, "config", "user.email", "parent-unmapped@example.com")
    _git(tmp_path, "commit", "--allow-empty", "-m", "parent package")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "update-ref", "refs/remotes/origin/parent", base)
    _git(tmp_path, "checkout", "-b", "child")
    _git(tmp_path, "config", "user.email", "new-author@example.com")
    _git(tmp_path, "commit", "--allow-empty", "-m", "child package")
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    original = audit.run
    def with_pr_metadata(*args, **kwargs):
        if args[:3] == ("gh", "pr", "view"):
            return json.dumps({"baseRefName": "parent"})
        return original(*args, **kwargs)
    monkeypatch.setattr(audit, "run", with_pr_metadata)
    explicit = base if base_source == "explicit" else None
    if base_source == "environment":
        monkeypatch.setenv("GITHUB_BASE_REF", "parent")
    assert audit.new_emails(explicit) == ["new-author@example.com"]
    if mapped:
        directory = tmp_path / "contributors/emails"
        directory.mkdir(parents=True)
        (directory / "new-author@example.com").write_text("fixture-author\n", encoding="utf-8")
    artifact = tmp_path / "review.json"
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    args = ["--review-status", str(artifact)] + (["--base", base] if explicit else [])
    assert audit.main(args) == (0 if mapped else 1)
    status = json.loads(artifact.read_text(encoding="utf-8-sig"))
    assert bool(status) is not mapped
    assert "inherited@example.com" not in artifact.read_text(encoding="utf-8-sig")
    assert "parent-unmapped@example.com" not in artifact.read_text(encoding="utf-8-sig")
    assert "review_status=" in output.read_text(encoding="utf-8-sig")


def test_unavailable_or_invalid_pr_base_never_falls_back_to_main(monkeypatch):
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    def fail(*args, **kwargs):
        raise RuntimeError("unavailable PR metadata")
    monkeypatch.setattr(audit, "run", fail)
    with pytest.raises(RuntimeError, match="actual PR base"):
        audit.resolve_base_ref()
    for invalid in ("-main", "main ref", ""):
        with pytest.raises(RuntimeError, match="Invalid attribution"):
            audit.resolve_base_ref(invalid)
