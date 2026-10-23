"""THE USAGE READING: the plan's limit shares, read by any agent without asking the user.

User input 68c33c7d (session 2026-10-22_08-28-56): usage windows are a measured, planned cost -- how much
of a 5-hour window (and of the weekly caps) a piece of work uses. Until session 2026-10-22_23-25-29 every
reading came from the user pasting /usage; Claude Code answers the same command in print mode, with no
model call:

    claude -p "/usage"   ->   "Current session: 63% used · resets Oct 23, 4:20am (America/Los_Angeles)" ...

This runs it from a throwaway directory (so no session lands in a project's transcript list), parses
every "<limit>: <n>% used · resets <when>" line, and prints one JSON object. --log appends it to
.cjm/usage-readings.jsonl (gitignored, swept by cg-backup) -- a dated observation, never overwritten,
so a window's share per piece of work is a difference of two readings. --label names what the reading
brackets (e.g. "before K1 challenger").

    python scripts/usage_reading.py [--log] [--label TEXT] [--raw]

Blind spots: the shares are the account's, across every machine and session -- a difference brackets
whatever else ran in between; the percentages are whole numbers, so a small agent reads as 0-1%.
"""

import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

LOG = pathlib.Path(__file__).resolve().parent.parent / '.cjm' / 'usage-readings.jsonl'
LIMIT = re.compile(r'^(?P<name>[^:\n]+?):\s*(?P<pct>\d+)% used(?:\s*·\s*resets (?P<resets>.+))?$', re.M)


def read():
    tmp = tempfile.mkdtemp(prefix='usage-reading-')
    try:
        out = subprocess.run(['claude', '-p', '/usage'], cwd=tmp, capture_output=True, text=True, timeout=120)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if out.returncode != 0:
        sys.exit(f'claude -p /usage failed ({out.returncode}): {out.stderr.strip()[:300]}')
    return out.stdout


def parse(text):
    limits = {m['name'].strip(): {'pct': int(m['pct']), 'resets': (m['resets'] or '').strip() or None}
              for m in LIMIT.finditer(text)}
    if not limits:
        sys.exit('no "<limit>: <n>% used" line in the /usage output -- its format changed; run with --raw')
    return limits


def main():
    args = sys.argv[1:]
    text = read()
    if '--raw' in args:
        print(text); return
    label = args[args.index('--label') + 1] if '--label' in args else None
    rec = {'at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'label': label, 'limits': parse(text)}
    if '--log' in args:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, 'a') as f: f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    print(json.dumps(rec, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
