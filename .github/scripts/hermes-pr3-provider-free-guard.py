"""Isolated verification harness: block real model construction and all test networking."""
import collections
import json
import pathlib
import sys

blocked = collections.Counter()
processes = collections.Counter()


def audit(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        blocked["network_attempts"] += 1
        raise RuntimeError("Provider-free verification forbids test networking")
    if event == "subprocess.Popen":
        command = args[1]
        if not isinstance(command, (list, tuple)) or not command:
            blocked["unclassified_process"] += 1
            raise RuntimeError("Provider-free verification requires classified argv")
        binary = pathlib.Path(str(command[0])).name.lower().removesuffix(".exe")
        processes[binary] += 1
        if binary not in {"git", "sysctl", "vm_stat"}:
            blocked["process_attempts"] += 1
            raise RuntimeError("Provider-free verification forbids this subprocess")
        if binary == "git" and any(str(a) in {"fetch", "push", "pull", "clone", "ls-remote"} for a in command[1:]):
            blocked["git_network_attempts"] += 1
            raise RuntimeError("Provider-free verification forbids Git transport during tests")


sys.addaudithook(audit)


def pytest_sessionstart(session):
    import run_agent

    def forbidden(*args, **kwargs):
        blocked["real_agent_attempts"] += 1
        raise RuntimeError("Provider-free verification forbids real AIAgent construction/conversation")

    # Tests may replace this class with their explicit mock or use an InputSink.
    # The genuine runtime class must never construct or converse in this harness.
    run_agent.AIAgent.__init__ = forbidden
    run_agent.AIAgent.run_conversation = forbidden


def pytest_sessionfinish(session, exitstatus):
    report = {
        "source_sha": "df972bf8adfa520fdf9a5db9e691ec4068e8c706",
        "test_file": pathlib.Path(session.config.args[0]).name,
        "exitstatus": int(exitstatus),
        "collected": session.testscollected,
        "blocked_attempts": dict(blocked),
        "subprocess_counts": dict(processes),
        "real_provider_calls": 0,
        "real_codex_calls": 0,
        "actual_delegate_spawns": 0,
        "limit": "Python audit + real-agent guard; explicit test doubles are allowed. Setup downloads are outside test execution.",
    }
    output = pathlib.Path(__file__).resolve().parents[2] / "verification-evidence"
    output.mkdir(exist_ok=True)
    (output / (report["test_file"] + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("PROVIDER_FREE_RECEIPT " + json.dumps(report))
