import argparse
import sys
from .service import EventService

def main(argv=None, *, service=None, stdout=None):
    parser = argparse.ArgumentParser(prog="events")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--query")
    commands.add_parser("count")
    args = parser.parse_args(argv)
    service = service if service is not None else EventService()
    stdout = stdout if stdout is not None else sys.stdout
    if args.command == "count":
        stdout.write(str(len(service.list_events())) + "\n")
    else:
        stdout.write(service.export_json(args.query) + "\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
