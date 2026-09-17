from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile

from .auth import CredentialStore
from .config import Settings
from .db import Database


class AdminError(Exception):
    pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Local OMP Remote administration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    issue_device = subparsers.add_parser("issue-device")
    issue_device.add_argument("--user", required=True)
    issue_device.add_argument("--label", default="")
    revoke_device = subparsers.add_parser("revoke-device")
    revoke_device.add_argument("device_id")
    issue_emergency = subparsers.add_parser("issue-emergency")
    issue_emergency.add_argument("--label", default="")
    revoke_emergency = subparsers.add_parser("revoke-emergency")
    revoke_emergency.add_argument("credential_id")
    subparsers.add_parser("disable")
    subparsers.add_parser("enable")
    subparsers.add_parser("status")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    database = Database(settings.db_path)
    database.initialize()
    credentials = CredentialStore(database, settings.allowed_users)

    if args.command == "status":
        print(json.dumps(_status(settings), separators=(",", ":")))
        return
    _require_root()
    settings.ensure_runtime_dirs()

    if args.command == "issue-device":
        device_id, token = credentials.issue_device(owner_user=args.user, label=args.label)
        print(json.dumps({"device_id": device_id, "token": token}, separators=(",", ":")))
    elif args.command == "revoke-device":
        if not credentials.revoke_device(args.device_id):
            raise SystemExit("device not found")
    elif args.command == "issue-emergency":
        credential_id, token = credentials.issue_emergency(label=args.label)
        print(json.dumps({"credential_id": credential_id, "token": token}, separators=(",", ":")))
    elif args.command == "revoke-emergency":
        if not credentials.revoke_emergency(args.credential_id):
            raise SystemExit("credential not found")
    elif args.command == "disable":
        _atomic_state(settings.state_path, "DISABLED")
        _record_incident(settings, "local_disable")
    elif args.command == "enable":
        _atomic_state(settings.state_path, "ENABLED")
    else:
        parser.error("unknown command")


def _require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("this administration command requires local root")


def _status(settings: Settings) -> dict[str, object]:
    try:
        state = settings.state_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        state = "ENABLED"
    except OSError:
        state = "DISABLED"
    return {"state": state if state in {"ENABLED", "DISABLED"} else "DISABLED"}


def _atomic_state(path: Path, state: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".state.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(state + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _record_incident(settings: Settings, action: str) -> None:
    incident = {
        "action": action,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = settings.data_dir / "incidents.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(json.dumps(incident, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
