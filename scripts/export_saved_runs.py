"""Export finished Bedrock runs of the client reference questions (scenarios 21-27) as bundled sample runs.

Reads your local chat history, picks the latest accepted run whose question is exactly a scenario's question,
and writes its full debug report (answer, evidence, every model call) to examples/saved-runs/.
Review the output before committing: the files are what every collaborator will see as sample chats.
"""
import argparse
import gzip
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
CLIENT_CASES = range(21, 28)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--history', type=Path, default=ROOT / 'artifacts/ui/history.sqlite')
    parser.add_argument('--output', type=Path, default=ROOT / 'examples/saved-runs')
    args = parser.parse_args()
    scenarios = {s['id']: s for s in json.loads((ROOT / 'examples/scenarios.json').read_text())['scenarios']}
    args.output.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(args.history)
    for number in CLIENT_CASES:
        question = scenarios[number]['question']
        chosen = None
        for stored in con.execute("SELECT debug_json FROM runs WHERE question=? AND status='done' AND debug_json IS NOT NULL "
                                  "ORDER BY created_at DESC", (question,)):
            report = json.loads(stored[0])
            result = report.get('result') or {}
            if (result.get('model_invoked') and not result.get('simulation')
                    and result.get('harness', {}).get('model', '').startswith('qwen.')
                    and result.get('scope', {}).get('question') == question):
                chosen = report
                break
        if chosen is None:
            print(f'scenario {number}: no accepted run in {args.history}; skipped')
            continue
        data = json.dumps(chosen, ensure_ascii=False, separators=(',', ':')).encode()
        with open(args.output / f'scenario-{number:02d}.debug.json.gz', 'wb') as handle:
            handle.write(gzip.compress(data, 9, mtime=0))
        print(f'scenario {number}: {len(data):,} bytes of JSON')


if __name__ == '__main__':
    main()
