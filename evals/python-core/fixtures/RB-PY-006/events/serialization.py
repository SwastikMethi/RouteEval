from dataclasses import asdict
import json

def events_to_json(events):
    return json.dumps([asdict(event) for event in events], ensure_ascii=False)
