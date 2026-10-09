"""Workflow data keeps portable runner/complete deterministic-shard contracts."""
from pathlib import Path
import re

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2] / ".github/workflows"


def _workflow(name):
    return YAML(typ="safe").load((ROOT / name).read_text(encoding="utf-8-sig"))


def test_ci_workflow_closure_has_no_unavailable_larger_runner_labels():
    pending, visited = ["ci.yaml"], set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        for job in _workflow(name)["jobs"].values():
            called = job.get("uses", "")
            if called.startswith("./.github/workflows/"):
                pending.append(called.rsplit("/", 1)[-1])
            assert "32-core" not in str(job.get("runs-on", ""))
            for row in job.get("strategy", {}).get("matrix", {}).get("include", []):
                assert "32-core" not in str(row.get("runner", ""))
                assert "32-arm-core" not in str(row.get("runner", ""))


def test_standard_runner_shards_cover_each_architecture_without_gaps():
    job = _workflow("tests.yml")["jobs"]["test"]
    run_step = next(s for s in job["steps"] if s.get("name") == "Run tests")
    match = re.search(r"/(\d+)$", run_step["env"]["HERMES_TEST_SLICE"])
    assert match is not None
    count = int(match[1])
    assert sorted(job["strategy"]["matrix"]["slice"]) == list(range(1, count + 1))
    assert job["strategy"]["fail-fast"] is False
    assert run_step["env"]["HERMES_TEST_WORKERS"] <= 4
    assert job["runs-on"] == "ubuntu-latest"
    windows = [row for row in _workflow("tests-os.yml")["jobs"]["os-tests"]["strategy"]["matrix"]["include"]
               if row["marker"] == "windows"]
    assert {row["runner"] for row in windows} == {"windows-latest", "windows-11-arm"}
    for runner in {row["runner"] for row in windows}:
        slices = [tuple(map(int, row["slice"].split("/"))) for row in windows if row["runner"] == runner]
        denominators = {n for _, n in slices}
        assert len(denominators) == 1
        assert sorted(i for i, n in slices) == list(range(1, next(iter(denominators)) + 1))
