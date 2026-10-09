#!/usr/bin/env python3
"""Audit (and auto-fix) contributor email mappings for a PR branch.

Mirrors the CI gate in .github/workflows/contributor-check.yml so salvage
branches never bounce off the check-attribution job. Run it from the branch
you are about to push:

    python3 scripts/audit_pr_attribution.py            # report only
    python3 scripts/audit_pr_attribution.py --fix      # create mapping files

Logic (kept in sync with contributor-check.yml):
  - scans commits relative to the actual PR base (--base, GITHUB_BASE_REF,
    or the current branch's GitHub PR metadata), never unrelated fork main
  - skips teknium/bot emails and ``<id>+<login>@users.noreply.github.com``
    (CI auto-resolves those)
  - everything else must have ``contributors/emails/<email>`` or a legacy
    AUTHOR_MAP entry in scripts/releases/authors_legacy.py

``--fix`` resolution order for an unmapped email:
  1. bare ``<login>@users.noreply.github.com`` → ``<login>``, verified via
     ``gh api users/<login>``. A warning is printed: the local part is
     *usually* the GitHub login but is user-controlled (the historical
     ``bryan@…`` → ``hydraxman`` case) — eyeball it against the PR author.
  2. ``gh api 'search/users?q=<email>+in:email'``
  3. otherwise: prints the manual ``add_contributor.py`` command and exits 1.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_SUBSTRINGS = (
    "teknium",
    "noreply@github.com",
    "dependabot",
    "github-actions",
    "anthropic.com",
    "cursor.com",
)
ID_NOREPLY_RE = re.compile(r"\d+\+.+@users\.noreply\.github\.com$")
BARE_NOREPLY_RE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})@users\.noreply\.github\.com$")


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        list(args), capture_output=True, text=True, encoding="utf-8",
        errors="replace", cwd=str(REPO_ROOT),
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve_base_ref(explicit: str | None = None) -> str:
    """No network needed in CI or with --base. Ambiguous local context fails closed."""
    ref = explicit
    if ref is None and os.environ.get("GITHUB_BASE_REF"):
        ref = "refs/remotes/origin/" + os.environ["GITHUB_BASE_REF"]
    if ref is None:
        try:
            metadata = json.loads(run("gh", "pr", "view", "--json", "baseRefName"))
            ref = "refs/remotes/origin/" + metadata["baseRefName"]
        except (RuntimeError, FileNotFoundError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("Cannot determine actual PR base; supply --base REF") from exc
    if not isinstance(ref, str) or not ref or ref.startswith("-") or any(c.isspace() for c in ref):
        raise RuntimeError("Invalid attribution base ref")
    return run("git", "rev-parse", "--verify", ref + "^{commit}")


def new_emails(base_ref: str | None = None) -> list[str]:
    selected = resolve_base_ref(base_ref)
    base = run("git", "merge-base", selected, "HEAD")
    log = run("git", "log", f"{base}..HEAD", "--format=%ae", "--no-merges")
    return sorted({e for e in log.splitlines() if e.strip()})


def is_mapped(email: str) -> bool:
    if any(s in email for s in SKIP_SUBSTRINGS):
        return True
    if ID_NOREPLY_RE.search(email):
        return True
    if (REPO_ROOT / "contributors" / "emails" / email).is_file():
        return True
    authors_py = REPO_ROOT / "scripts" / "releases" / "authors_legacy.py"
    try:
        if f'"{email}"' in authors_py.read_text(encoding="utf-8-sig", errors="replace"):
            return True
    except OSError:
        pass
    return False


def gh_json(*args: str):
    try:
        out = run("gh", "api", *args, check=False)
        return json.loads(out) if out else None
    except (RuntimeError, json.JSONDecodeError, FileNotFoundError):
        return None


def resolve_login(email: str) -> tuple[str, str] | None:
    """Return (login, how) or None."""
    m = BARE_NOREPLY_RE.match(email)
    if m:
        login = m.group(1)
        user = gh_json(f"users/{login}")
        if user and user.get("login"):
            return user["login"], "bare-noreply local part (verified user exists)"
    found = gh_json(f"search/users?q={email}+in:email")
    if found and found.get("items"):
        return found["items"][0]["login"], "GitHub email search"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true",
                        help="auto-create contributors/emails/ mapping files")
    parser.add_argument("--base", help="actual PR base ref or exact commit; required when no PR metadata is available")
    parser.add_argument("--review-status", type=Path, help="write the CI review-status JSON artifact and step output")
    args = parser.parse_args(argv)

    try:
        unmapped = [e for e in new_emails(args.base) if not is_mapped(e)]
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.review_status is not None:
        statuses = [] if not unmapped else [{"source": "contributor attribution", "results": [{
            "kind": "action_required", "title": "Unmapped contributor email(s)",
            "summary": "New PR contributor email(s) have no mapping.",
            "detail": "\n".join(unmapped),
            "how_to_fix": "Run scripts/audit_pr_attribution.py --base ACTUAL_BASE --fix, or scripts/add_contributor.py EMAIL VERIFIED_LOGIN; preserve contributor identity.",
        }]}]
        serialized = json.dumps(statuses, ensure_ascii=False, separators=(",", ":"))
        args.review_status.write_text(serialized + "\n", encoding="utf-8")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write("review_status=" + serialized + "\n")
    if not unmapped:
        print("✅ All contributor emails on this branch are mapped.")
        return 0

    failed = []
    for email in unmapped:
        author = run("git", "log", f"--author={email}", "--format=%an", "-1", check=False)
        if not args.fix:
            print(f"⚠️  unmapped: {email} ({author})")
            continue
        resolved = resolve_login(email)
        if resolved:
            login, how = resolved
            run("python3", "scripts/add_contributor.py", email, login)
            print(f"✔ mapped {email} -> {login}  [{how}]")
            if BARE_NOREPLY_RE.match(email):
                print(f"  ⚠ local part is user-controlled — confirm @{login} really is "
                      f"the contributor (git name: {author!r}) before pushing.")
        else:
            failed.append((email, author))

    if not args.fix:
        print("\nRun with --fix to auto-create mapping files, or manually:")
        for email in unmapped:
            print(f"    python3 scripts/add_contributor.py {email} <github-username>")
        return 1

    if failed:
        print("\nCould not auto-resolve; map manually:")
        for email, author in failed:
            print(f"    python3 scripts/add_contributor.py {email} <github-username>  # {author}")
        return 1

    print("\nDone — remember to `git add contributors && git commit`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
