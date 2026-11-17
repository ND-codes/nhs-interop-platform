"""Count messages in a Mailpit /api/v1/messages payload, defensively.

The walkthrough asserts that the inbox is reachable. The number held is
reported for context only, so this never raises: Mailpit's exact response
shape was not verified against its documentation when this was written, and a
schema change should not turn a working inbox into a failed tour.
"""
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("?")
    raise SystemExit(0)

for key in ("messages_count", "total", "count"):
    if isinstance(data, dict) and isinstance(data.get(key), int):
        print(data[key])
        raise SystemExit(0)

messages = data.get("messages") if isinstance(data, dict) else data
print(len(messages) if isinstance(messages, list) else "?")
