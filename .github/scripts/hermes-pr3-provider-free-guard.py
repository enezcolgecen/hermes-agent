"""Isolated verification harness: block real model construction and all test networking."""
import collections
import json
import pathlib
import socket
import sys
import traceback
import weakref

blocked = collections.Counter()
processes = collections.Counter()
listeners = weakref.WeakSet()
internal_socketpairs = 0


def process_argv(command):
    if isinstance(command, (list, tuple)) and command:
        return command
    if sys.platform == "win32" and isinstance(command, str):
        # Windows audit receives CreateProcess command-line text even when the
        # caller supplied argv. Use the OS parser, never POSIX shlex or a shell.
        import ctypes
        from ctypes import wintypes
        count = ctypes.c_int()
        parser = ctypes.windll.shell32.CommandLineToArgvW
        parser.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        parser.restype = ctypes.POINTER(wintypes.LPWSTR)
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [wintypes.HLOCAL]
        free.restype = wintypes.HLOCAL
        values = parser(command, ctypes.byref(count))
        if values:
            try:
                return [values[i] for i in range(count.value)]
            finally:
                free(ctypes.cast(values, wintypes.HLOCAL))
    raise RuntimeError("Provider-free verification requires classified argv")


def audit(event, args):
    global internal_socketpairs
    if event == "socket.bind":
        address = args[1]
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"} and address[1] == 0:
            listeners.add(args[0])
    if event == "socket.connect":
        address = args[1]
        # Windows asyncio uses the stdlib's owned TCP socketpair for its wakeup
        # pipe. Accept only that exact stdlib frame and same-process listener.
        stdlib_pair = any(frame.name == "_fallback_socketpair" and
                          pathlib.Path(frame.filename).resolve() == pathlib.Path(socket.__file__).resolve()
                          for frame in traceback.extract_stack())
        if stdlib_pair and isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            for listener in list(listeners):
                try:
                    if listener.getsockname() == address:
                        internal_socketpairs += 1
                        return
                except OSError:
                    pass
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        blocked["network_attempts"] += 1
        raise RuntimeError("Provider-free verification forbids test networking")
    if event == "subprocess.Popen":
        try:
            command = process_argv(args[1])
        except RuntimeError:
            blocked["unclassified_process"] += 1
            raise
        # An explicit executable overrides argv[0] in CreateProcess/Popen.
        binary = pathlib.Path(str(args[0] or command[0])).name.lower().removesuffix(".exe")
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
        "stdlib_owned_socketpair_connections": internal_socketpairs,
        "real_provider_calls": 0,
        "real_codex_calls": 0,
        "actual_delegate_spawns": 0,
        "limit": "Python audit + real-agent guard; explicit test doubles are allowed. Setup downloads are outside test execution.",
    }
    output = pathlib.Path(__file__).resolve().parents[2] / "verification-evidence"
    output.mkdir(exist_ok=True)
    (output / (report["test_file"] + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("PROVIDER_FREE_RECEIPT " + json.dumps(report))
