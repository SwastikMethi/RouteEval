# Event service
Events are `Event(id, title, notes, attendees)` records. Add CSV serialization
as `events.serialization.events_to_csv(events)` and service method
`EventService.export_csv(query=None)`. Export the service's existing filtered
list, preserving its order. Always emit header `id,title,notes,attendees`, even
for empty data. Use Python's csv writer with comma delimiter, minimal quoting,
and CRLF line endings (including the final record) so commas, quotes, CR, LF,
and Unicode round-trip correctly. Do not alter record values or input lists.

Add `--format {json,csv}` to the `list` CLI subcommand; default remains JSON.
`--query` applies to either format. CSV output must come through the service's
export_csv boundary, not by serializing domain records in cli.py. Existing
`count`, JSON output, and `main(argv=None, *, service=None, stdout=None)` remain
compatible. The CLI returns 0 on success and writes no diagnostics to stdout.
Change only `events/`. Run `python -m pytest -q tests`.
