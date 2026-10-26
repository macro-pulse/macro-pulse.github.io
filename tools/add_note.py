#!/usr/bin/env python3
"""Append a note to notes.json. Body is read from stdin (simple markdown).
Usage: echo "Body text" | python3 tools/add_note.py --title "Title" --tags "fed,rates" [--date YYYY-MM-DD] [--time "9:30 am"]"""
import argparse, json, os, sys, uuid
from datetime import datetime
from zoneinfo import ZoneInfo

p = argparse.ArgumentParser()
p.add_argument("--title", default=""); p.add_argument("--tags", default="")
p.add_argument("--date", default=""); p.add_argument("--time", default="")
a = p.parse_args()
now = datetime.now(ZoneInfo("America/New_York"))
body = sys.stdin.read().strip()
if not body: sys.exit("empty body")
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "notes.json")
notes = json.load(open(path)) if os.path.exists(path) else []
n = dict(id=uuid.uuid4().hex[:8], date=a.date or now.strftime("%Y-%m-%d"), title=a.title, body=body,
         tags=[t.strip() for t in a.tags.split(",") if t.strip()])
if a.time: n["time"] = a.time + ("" if a.time.upper().endswith("ET") else " ET")
notes.append(n)
json.dump(notes, open(path, "w"), indent=1, ensure_ascii=False); open(path, "a").write("\n")
print("added", n["id"], n["date"])
