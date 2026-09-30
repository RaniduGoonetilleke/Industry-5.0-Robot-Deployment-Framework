"""Check the selector, saved examples, decision table and recorded admission decisions.

All checks run offline. No robot or simulator is started."""
import sys
sys.dont_write_bytecode = True
import hashlib, json, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'verification'
OUT.mkdir(exist_ok=True)
def run(args):
    result = subprocess.run([sys.executable, '-B', *args], cwd=ROOT, text=True, capture_output=True)
    print(result.stdout, end=''); print(result.stderr, end='', file=sys.stderr)
    if result.returncode: raise SystemExit(result.returncode)
run(['-m', 'unittest', 'discover', '-s', 'selector', '-p', 'test_selector.py'])
run(['selector/build_examples.py'])
run(['scripts/check_table2.py'])
sys.path.insert(0, str(ROOT / 'historical'))
import decision_prototype as P
trace = json.loads((ROOT / 'records/DECISION_TRACE.json').read_text())
assert hashlib.sha256((ROOT / 'historical/decision_prototype.py').read_bytes()).hexdigest() == trace['prototype_sha256']
rows = []
for entry in trace['part_A_g1_locations']:
    for stage in ('pre_run_only', 'with_selection'):
        facts = entry['pre_run_only']['facts'] if stage == 'pre_run_only' else entry['facts']
        confirmed = False if stage == 'pre_run_only' else entry['confirmed']
        expected = entry['pre_run_only']['action'] if stage == 'pre_run_only' else entry['framework_action']
        actual = P.decide(facts, True, confirmed)['action']
        assert actual == expected == P.reference(facts, True, confirmed), (entry['tag'], stage, actual, expected)
        rows.append({'location': entry['tag'], 'stage': stage, 'action': actual})
record = {'scope': 'Recomputed software actions on frozen retrospective inputs; no re-derivation of source facts or prospective trial.',
          'snapshots': len(rows), 'all_match': True, 'rows': rows}
(OUT / 'TRACE_REPRODUCTION.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps({'trace_snapshots':len(rows), 'all_match':True}))
print('PASS: offline selector tests, 29 examples, Table 2 and 20 saved trace snapshots. No native simulation was launched.')
