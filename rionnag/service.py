"""Per-user Windows supervisor for the bot and configured scrim collector."""

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from dotenv import dotenv_values

from rionnag.instance import SingleInstance

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "service"


def write_json(path, value):
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def status():
    try:
        value = json.loads((STATE / "status.json").read_text(encoding="utf-8"))
        if time.time() - value["heartbeat"] > 10:
            return {"state": "offline"}
        return value
    except (OSError, ValueError, KeyError):
        return {"state": "offline"}


def stop_child(child):
    if child is not None and child.poll() is None:
        # Windows venv launchers may own a second Python process. Kill only
        # this live Popen child's tree, never processes selected by name.
        subprocess.run(
            ["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=15)


def managed_commands():
    commands = {"bot": ([str(ROOT / ".venv/Scripts/python.exe"), "-m", "rionnag"], ROOT)}
    collector_root = ROOT / "scrim_collector"
    settings = dotenv_values(collector_root / ".env")
    if (settings.get("DISCORD_USER_TOKEN") or "").strip() and (
            settings.get("SCRIM_SOURCE_CHANNEL_IDS") or "").strip():
        commands["collector"] = (
            [str(collector_root / ".venv/Scripts/python.exe"), str(collector_root / "main.py")],
            collector_root,
        )
    return commands


def supervise():
    STATE.mkdir(parents=True, exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    with SingleInstance(STATE / "supervisor.lock"):
        commands = managed_commands()
        children = dict.fromkeys(commands)
        next_start = dict.fromkeys(commands, 0)
        last_exits = dict.fromkeys(commands)
        acknowledgement = None
        stopping = False
        try:
            with (ROOT / "logs" / "service.log").open("ab", buffering=0) as output:
                while not stopping:
                    acknowledgements = []
                    # Unique request files avoid concurrent callers overwriting requests.
                    for request in sorted(STATE.glob("request-*.json")):
                        try:
                            action = json.loads(request.read_text(encoding="utf-8"))["action"]
                        except (OSError, ValueError, KeyError):
                            request.unlink(missing_ok=True)
                            continue
                        request.unlink(missing_ok=True)
                        if action not in {"restart", "stop"}:
                            continue
                        for child in children.values():
                            stop_child(child)
                        commands = managed_commands()
                        children = dict.fromkeys(commands)
                        next_start = dict.fromkeys(commands, 0)
                        last_exits = {name: last_exits.get(name) for name in commands}
                        acknowledgement = request.stem
                        stopping = action == "stop"
                        acknowledgements.append((request.stem, action))
                        if stopping:
                            break
                    for name, (args, cwd) in commands.items():
                        child = children[name]
                        if child is not None and child.poll() is not None:
                            last_exits[name] = child.returncode
                            children[name] = None
                            next_start[name] = time.monotonic() + 15
                        if not stopping and children[name] is None and time.monotonic() >= next_start[name]:
                            try:
                                children[name] = subprocess.Popen(
                                    args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                    creationflags=subprocess.CREATE_NO_WINDOW,
                                )
                            except OSError as exc:
                                output.write(f"Could not start {name}: {type(exc).__name__}\n".encode())
                                next_start[name] = time.monotonic() + 15
                    write_json(STATE / "status.json", {
                        "state": "stopped" if stopping else "running" if children["bot"] else "retrying",
                        "supervisor_pid": os.getpid(),
                        "bot_pid": children["bot"].pid if children["bot"] else None,
                        "collector_state": "disabled" if "collector" not in children else (
                            "stopped" if stopping else "running" if children["collector"] else "retrying"
                        ),
                        "collector_pid": children["collector"].pid if children.get("collector") else None,
                        "collector_last_exit": last_exits.get("collector"),
                        "heartbeat": time.time(), "last_exit": last_exits["bot"],
                        "acknowledgement": acknowledgement,
                    })
                    for request_id, action in acknowledgements:
                        write_json(STATE / f"ack-{request_id}.json", {"action": action})
                    if not stopping:
                        time.sleep(1)
        finally:
            for child in children.values():
                stop_child(child)
            write_json(STATE / "status.json", {
                "state": "stopped", "heartbeat": time.time(), "bot_pid": None, "collector_pid": None,
                "collector_state": "stopped" if "collector" in commands else "disabled",
            })


def start():
    if status()["state"] in {"running", "retrying"}:
        return
    subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "pythonw.exe"), "-m", "rionnag.service", "run"],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if status()["state"] in {"running", "retrying"}:
            return
        time.sleep(0.25)
    raise RuntimeError("Supervisor did not start; inspect logs/service.log.")


def control(action):
    if status()["state"] not in {"running", "retrying"}:
        if action == "restart":
            start()
        return
    request = STATE / f"request-{uuid.uuid4().hex}.json"
    write_json(request, {"action": action})
    ack = STATE / f"ack-{request.stem}.json"
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if ack.exists():
            ack.unlink()
            return
        time.sleep(0.25)
    request.unlink(missing_ok=True)
    raise RuntimeError("Supervisor did not acknowledge the request.")


def install():
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    startup.mkdir(parents=True, exist_ok=True)
    # WScript launches the supervisor hidden, outside any chat's terminal.
    command = f'"{ROOT / ".venv/Scripts/pythonw.exe"}" -m rionnag.service run'
    def quote(value):
        return '"' + str(value).replace('"', '""') + '"'

    (startup / "RionnagBot.vbs").write_text(
        'Set shell = CreateObject("WScript.Shell")\n'
        f"shell.CurrentDirectory = {quote(ROOT)}\n"
        f"shell.Run {quote(command)}, 0, False\n", encoding="utf-8",
    )
    start()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "start", "restart", "stop", "status", "run"])
    action = parser.parse_args().action
    if os.name != "nt":
        parser.error("This per-user service requires Windows.")
    STATE.mkdir(parents=True, exist_ok=True)
    if action == "run":
        supervise()
        return
    if action == "install":
        install()
    elif action == "start":
        start()
    elif action in {"restart", "stop"}:
        control(action)
    print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()
