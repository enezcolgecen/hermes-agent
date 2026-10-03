---
name: github
description: "GitHub via gh CLI: PRs, issues, reviews, repos, auth."
version: 2.0.0
author: Ben Barclay (benbarclay), Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [github, gh, git, pull-requests, issues, code-review, repos, auth, ci]
    category: software-development
    related_skills: [codebase-inspection, requesting-code-review]
---

# GitHub

Work GitHub end to end with the `gh` CLI (REST fallback where noted): auth,
issues, the PR lifecycle, issue-to-PR delivery, code review, and repo
management. This skill consolidates six former skills; each workflow lives
complete in its reference file — ALWAYS read the matching reference before
starting that workflow, the body below only routes.

## Routing

| Task | Read first |
|---|---|
| Auth broken / new machine / token or SSH setup / gh login | `references/auth.md` |
| Create, triage, label, assign, close issues | `references/issues.md` |
| Branch, commit, open PR, watch CI, merge | `references/pr-workflow.md` |
| Carry an ISSUE to a verified PR (full delivery loop) | `references/issue-to-pr.md` |
| Review someone's PR: diffs, inline comments, verdict | `references/code-review.md` |
| Clone/create/fork repos, remotes, releases | `references/repo-management.md` |

Supporting assets: `scripts/gh-env.sh` + `scripts/git-credential-token.py`
(auth helpers), `templates/` (PR bodies, bug report, feature request),
`references/ci-troubleshooting.md`, `references/conventional-commits.md`,
`references/github-api-cheatsheet.md`, `references/review-output-template.md`.

## Core discipline (applies to every workflow)

- Preflight once per session: `gh auth status` — if it fails, go to
  `references/auth.md` before anything else.
- Prefer `gh` over raw REST; drop to `gh api` only for endpoints the
  porcelain lacks (the cheatsheet lists them).
- Never report CI green without checking `gh pr checks` yourself; never
  claim merged without verifying `state,mergedAt`.
- Read full context before writing: `gh issue view --comments` /
  `gh pr view --comments` — decisions live in threads, not titles.
- Sweep for duplicates before creating anything:
  `gh pr list --search` / `gh issue list --search`.

## Read-only status fast path

For read-only repository, pull-request, issue, branch, CI, or remote-status questions, stay headless and use structured CLI output.

- Never use `--web`, `gh browse`, or open a browser unless the user explicitly asks for browser navigation.
- Never use general web search merely to discover a GitHub repository that can be resolved from task context, a local Git remote, authenticated GitHub identity, or `gh repo list`.
- Local session or memory state may identify the repository, but mutable GitHub state such as open, draft, merge, or check status must still be read fresh with `gh`.
- Resolve repository identity in this order: exact `owner/repo` from task context; current checkout via `git remote get-url origin`; authenticated account plus `gh repo list`; then report resolution failure.
- CutMentis routing aliases: `CutMentis control-plane` -> `enezcolgecen/cutmentis-control-plane`; `CutMentis product` or `cutiq-platform` -> `enezcolgecen/cutiq-platform`.
- For an open-PR status request, start with: `gh pr list --repo OWNER/REPO --state open --json number,title,state,isDraft,mergeStateStatus,baseRefName,headRefName,createdAt,url`.
- Do not run `gh pr view` for every PR when the list response already contains the fields needed to answer.
- If an individual PR read is necessary, use structured `gh pr view ... --json`. Do not add `--web` unless browser navigation was explicitly requested.

## Verification

- The workflow's own reference file defines done for that task.
- Cross-cutting: every claim about remote state (CI, merge, release,
  issue state) is backed by a fresh `gh` read, never memory.
