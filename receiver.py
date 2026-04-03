import json
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


# Update these values directly on the Linux PC before each run if needed.
UDP_BIND_HOST = "0.0.0.0"
UDP_BIND_PORT = 5005
COMMAND_COOLDOWN_SEC = 25.0
SCRIPT_MAP = {
    "CLENCH": "eat_elite.py",
    "BLINK": "drink_piper.py",
}


def _log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


def _extract_command(packet: bytes) -> tuple[str | None, dict[str, Any]]:
    try:
        payload = json.loads(packet.decode("utf-8"))
    except Exception:
        raw = packet.decode("utf-8", errors="ignore").strip()
        if not raw:
            return None, {}
        return raw.upper(), {"raw": raw}

    if isinstance(payload, dict):
        command = payload.get("cmd", payload.get("command", payload.get("decision")))
        if command is None:
            return None, payload
        return str(command).upper(), payload

    return str(payload).upper(), {"raw": payload}


def _run_action_script(command: str) -> None:
    script_name = SCRIPT_MAP.get(command)
    if not script_name:
        return

    script_path = Path(__file__).resolve().parent / script_name
    if not script_path.exists():
        _log(f"action script not found for {command}: {script_path}")
        return

    _log(f"starting action script for {command}: {script_path.name}")
    try:
        subprocess.run(
            [sys.executable, str(script_path)],
            check=True,
        )
        _log(f"action script finished for {command}: {script_path.name}")
    except subprocess.CalledProcessError as exc:
        _log(
            f"action script failed for {command}: {script_path.name} "
            f"(exit={exc.returncode})"
        )
    except Exception as exc:
        _log(f"action script failed for {command}: {exc}")


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_BIND_HOST, UDP_BIND_PORT))
    busy_until = 0.0

    _log(f"listening for UDP commands on {UDP_BIND_HOST}:{UDP_BIND_PORT}")

    try:
        while True:
            packet, addr = sock.recvfrom(4096)
            command, payload = _extract_command(packet)
            if not command:
                _log(f"received empty payload from {addr[0]}:{addr[1]}")
                continue

            now = time.monotonic()
            if command in {"BLINK", "CLENCH", "NEUTRAL"} and now < busy_until:
                _log(
                    "Previous command is not finished. "
                    f"Ignored {command} from {addr[0]}:{addr[1]} payload={payload}"
                )
                continue

            if command == "TEST":
                _log(
                    f"received TEST from {addr[0]}:{addr[1]} "
                    f"payload={payload}"
                )
                continue

            _log(
                f"received {command} from {addr[0]}:{addr[1]} "
                f"payload={payload}"
            )

            if command in {"BLINK", "CLENCH"}:
                busy_until = now + COMMAND_COOLDOWN_SEC
                _log(
                    f"started {command} cooldown for {COMMAND_COOLDOWN_SEC:.1f}s"
                )
                worker = threading.Thread(
                    target=_run_action_script,
                    args=(command,),
                    name=f"action-{command.lower()}",
                    daemon=True,
                )
                worker.start()
    except KeyboardInterrupt:
        _log("stopped by user")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
