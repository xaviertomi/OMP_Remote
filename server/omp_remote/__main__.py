from __future__ import annotations

import argparse

from .app import serve
from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="OMP Remote local service")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="run the localhost API")
    subparsers.add_parser("emergency", help="run the independent emergency API")
    subparsers.add_parser("gateway", help="run the private API/emergency gateway")
    worker = subparsers.add_parser("worker", help="run one owner worker")
    worker.add_argument("--omp-binary", required=True)
    worker.add_argument("--owner-user", required=True)
    args = parser.parse_args()
    if args.command == "serve":
        serve(Settings.from_env())
    elif args.command == "worker":
        from .worker_main import main as worker_main

        worker_main(["--owner-user", args.owner_user, "--omp-binary", args.omp_binary])
    elif args.command == "emergency":
        from .emergency_app import serve_emergency

        serve_emergency(Settings.from_env())
    else:
        from .gateway import serve_gateway

        serve_gateway()


if __name__ == "__main__":
    main()
