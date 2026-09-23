#!/usr/bin/env python3
"""SessionStart: print protocol-55.md with this plugin's path and the session id filled in."""
import json, os, sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    sid = json.load(sys.stdin).get("session_id", "<session id>")
except Exception:
    sid = "<session id>"
print(open(os.path.join(root, "protocol-55.md")).read().replace("{ROOT}", root).replace("{SID}", sid))
