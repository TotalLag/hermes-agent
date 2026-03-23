#!/usr/bin/env python3
import json
import sys

data = json.load(open(sys.argv[1]))
for w in data.get("workers", []):
    print(f"  - {w['name']} ({w['id']}): {w['status']}")
if not data.get("workers"):
    print("  (no workers registered)")
