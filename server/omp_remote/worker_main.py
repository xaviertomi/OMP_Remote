from __future__ import annotations

import argparse
import os
from pathlib import Path
import pwd

from .config import Settings
from .db import Database
from .files import FileStore
from .runner import OMPRunner
from .worker import Worker
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run one OMP Remote owner worker")
    parser.add_argument("--owner-user", required=True)
    parser.add_argument("--omp-binary", required=True)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.owner_user not in settings.allowed_users:
        parser.error("owner user is not allowlisted")
    try:
        account = pwd.getpwnam(args.owner_user)
    except KeyError:
        parser.error("owner Unix account does not exist")
    if os.geteuid() != account.pw_uid:
        parser.error("worker must run as its configured Unix owner")
    database = Database(settings.db_path)
    database.initialize()
    worker = Worker(
        database=database,
        owner_user=args.owner_user,
        runner=OMPRunner(omp_binary=Path(args.omp_binary)),
        state_path=settings.state_path,
        file_store=FileStore(
            database,
            workspace_root=settings.workspace_root,
            max_file_bytes=settings.max_file_bytes,
            max_job_bytes=settings.max_job_bytes,
            max_total_bytes=settings.max_total_bytes,
        ),
        max_concurrent_per_user=settings.max_concurrent_per_user,
        max_concurrent_global=settings.max_concurrent_global,
    )
    worker.run_forever()
if __name__ == "__main__":
    main()
