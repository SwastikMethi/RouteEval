import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import Settings
from .security import sanitize


async def validate_suite(settings, suite_id):
    from .grading import grade
    from .security import clean_environment, owned_delete
    from .workspace import capture, prepare, release

    suite = settings.suites[suite_id]
    results = []
    for case in suite["cases"]:
        row = {"case": case["id"]}
        for variant in ("baseline", "reference"):
            workspace = prepare(case, settings.workspaces, "validation", case["id"] + "-" + variant)
            try:
                if variant == "reference":
                    process = await asyncio.create_subprocess_exec(
                        "git",
                        "apply",
                        case["reference_patch"],
                        cwd=workspace,
                        env=clean_environment(),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await process.communicate()
                    if process.returncode:
                        raise ValueError(sanitize(stderr.decode(errors="replace")))
                result = await grade(case, workspace, capture(workspace), settings)
                valid = not result["infrastructure_error"] and (
                    result["full_pass"] if variant == "reference" else not result["full_pass"]
                )
                row[variant] = valid
            finally:
                release(workspace)
                owned_delete(settings.workspaces, workspace)
        results.append(row)
    print(json.dumps(results, indent=2))
    return all(row["baseline"] and row["reference"] for row in results)


def main():
    parser = argparse.ArgumentParser(description="RouteBench local coding-agent evaluation")
    parser.add_argument("--config", type=Path, help="YAML configuration path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Validate suites and run version/help preflight; no model calls")
    validation = commands.add_parser("validate-suite", help="Check baselines/reference patches offline")
    validation.add_argument("suite", nargs="?", default="python-core")
    commands.add_parser("serve", help="Serve dashboard and API on localhost")
    costs = commands.add_parser("backfill-costs", help="Preview historical Codex estimates offline; server must be stopped")
    costs.add_argument("--apply", action="store_true", help="Back up the database and save the estimates")
    args = parser.parse_args()
    try:
        settings = Settings(args.config)
        if args.command == "serve":
            import uvicorn

            from .api import create_app

            uvicorn.run(
                create_app(settings),
                host=settings.server["host"],
                port=settings.server["port"],
                log_level="warning",
            )
        elif args.command == "doctor":
            from . import adapters

            async def check():
                profiles = []
                for profile in settings.profiles.values():
                    profiles.append(
                        await adapters.preflight(profile)
                        if profile["adapter"] != "mock"
                        else {"profile_id": profile["id"], "status": "ready", "version": "test-only"}
                    )
                return profiles

            print(
                json.dumps(
                    sanitize(
                        {
                            "profiles": asyncio.run(check()),
                            "suites": [
                                {
                                    "id": s["id"],
                                    "cases": len(s["cases"]),
                                    "version": s["version"],
                                    "hash": s["hash"],
                                }
                                for s in settings.suites.values()
                            ],
                        }
                    ),
                    indent=2,
                )
            )
        elif args.command == "backfill-costs":
            from .backfill_costs import backfill_costs

            print(json.dumps(backfill_costs(settings, apply=args.apply), indent=2))
        else:
            settings.workspaces.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not asyncio.run(validate_suite(settings, args.suite)):
                sys.exit(1)
    except (ValueError, OSError, KeyError) as exc:
        print(f"RouteBench: {sanitize(str(exc))}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
