#!/bin/bash
# Bounded single-request probe. Usage: probe.sh <saved_name> <url> [note]
# Saves raw bytes to $DL/<saved_name>, appends one JSON line to probes.jsonl.
# Plain curl (default UA), sleeps 1.2 s before each request (per-host politeness).
set -u
EV="$(cd "$(dirname "$0")" && pwd)"
DL="${S1R_DL:-/private/tmp/claude-501/-Users-andry-Desktop-nba-predictor-sprint1/11c8ef97-c1df-493c-b7b0-edeb8138cb0b/scratchpad/s1-recheck-downloads}"
name="$1"; url="$2"; note="${3:-}"
out="$DL/$name"
sleep 1.2
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
status="$(curl -sS -L --max-time 60 -o "$out" -w '%{http_code}' "$url" 2>"$out.err")" || status="${status:-000}"
[ -s "$out.err" ] || rm -f "$out.err"
[ -f "$out" ] || : > "$out"
python3 -I - "$url" "$ts" "$status" "$out" "$note" >> "$EV/probes.jsonl" <<'PY'
import sys, json, hashlib, os, urllib.parse
url, ts, status, out, note = sys.argv[1:6]
b = open(out, 'rb').read()
q = urllib.parse.urlsplit(url).query
print(json.dumps({"url": url, "params": dict(urllib.parse.parse_qsl(q)), "method": "GET",
                  "retrieved_at": ts, "http_status": int(status) if status.isdigit() else status,
                  "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest(),
                  "saved_path": out, "note": note}))
PY
echo "$status $(wc -c < "$out") $name"
