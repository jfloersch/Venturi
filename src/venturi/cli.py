import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .geometry import fixture_selection, inspect_step
from .models import PipeSpec, write_json
from .runtime import diagnostics
from .workflows import mesh_spike, reference


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Venturi milestone 0: inspect, mesh, solve, and report."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check CAD and the pinned solver runtime")
    for name in ("reference", "mesh-spike"):
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path)
    p = sub.add_parser("inspect")
    p.add_argument("step", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("serve")
    p.add_argument("--storage", type=Path, default=Path("artifacts/workbench"))
    p.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            from .server import serve

            serve(args.storage, args.port)
        elif args.command == "doctor":
            value = diagnostics()
            print(json.dumps(value, indent=2))
            if any(c["status"] == "fail" for c in value["checks"]):
                sys.exit(1)
        elif args.command == "inspect":
            value = inspect_step(args.step)
            write_json(args.output, value)
            print(
                f"Inspected {len(value['faces'])} faces; geometry {value['geometry_hash'][:12]}; units: metres."
            )
        else:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            folder = args.output or Path("artifacts") / f"{args.command}-{stamp}"
            if args.command == "reference":
                value = reference(folder, PipeSpec(), progress=lambda s: print(s, flush=True))
            else:
                source = Path(__file__).resolve().parents[2] / "fixtures/pipe.step"
                geometry = inspect_step(source)
                value = mesh_spike(
                    folder,
                    geometry,
                    fixture_selection(geometry),
                    source,
                    progress=lambda s: print(s, flush=True),
                )
            print(f"{value['status'].upper()}: {folder.resolve() / 'report.html'}")
            if value["status"] != "passed":
                sys.exit(1)
    except (ValueError, RuntimeError, TimeoutError, OSError) as e:
        print(f"Venturi stopped: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
