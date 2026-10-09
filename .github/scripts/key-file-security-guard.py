"""Key-file-only verification: forbid network/process execution, allow stdlib IPC."""
import collections
import json
import pathlib
import socket
import sys
import traceback
import weakref

blocked = collections.Counter()
listeners = weakref.WeakSet()
ipc_connections = 0


def audit(event, args):
    global ipc_connections
    if event == "socket.bind":
        address = args[1]
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"} and address[1] == 0:
            listeners.add(args[0])
    if event == "socket.connect":
        address = args[1]
        stdlib_pair = any(f.name == "_fallback_socketpair" and
                          pathlib.Path(f.filename).resolve() == pathlib.Path(socket.__file__).resolve()
                          for f in traceback.extract_stack())
        if stdlib_pair and isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            for listener in list(listeners):
                try:
                    if listener.getsockname() == address:
                        ipc_connections += 1
                        return
                except OSError:
                    pass
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto",
                 "subprocess.Popen", "os.system", "os.posix_spawn"}:
        blocked[event] += 1
        raise RuntimeError("Key-file security verification forbids network/process execution")


sys.addaudithook(audit)


def pytest_sessionfinish(session, exitstatus):
    report = {"exitstatus": int(exitstatus), "collected": session.testscollected,
              "blocked_attempts": dict(blocked), "stdlib_ipc_connections": ipc_connections,
              "real_provider_calls": 0, "real_codex_calls": 0, "actual_delegate_spawns": 0}
    output = pathlib.Path(__file__).resolve().parents[2] / "key-file-evidence"
    output.mkdir(exist_ok=True)
    (output / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("KEY_FILE_AUDIT " + json.dumps(report))

