"""Check that the decision table and selector give the same answers.

Each printed condition is matched to a known predicate. Rows are checked in
their stated order across 288 combinations of safety, evidence, availability
and declarations. Two deliberately incorrect tables must disagree with the
selector. The output is verification/TABLE2_SEMANTICS.json.

Run from the package root: python3 -B scripts/check_table2.py"""
import sys; sys.dont_write_bytecode = True
import copy, hashlib, itertools, json, re
from pathlib import Path
R21 = Path(__file__).resolve().parents[1]
REC = R21
sys.path.insert(0, str(REC / 'selector'))
from selector import select                      # noqa: E402
from build_examples import request_for, live_for  # noqa: E402

PROFILES = {p['id']: p for p in json.loads((REC / 'analysis/CANDIDATE_MAPPING.json').read_text())['profiles']}

# Clause vocabulary: exact printed clause -> predicate over the abstract state s.
VOCAB = {
    'a shared work-area hazard': lambda s: s['shared'] == 'FAIL',
    'shared safety PASS and a nonempty in-scope set whose candidates all fail a check':
        lambda s: s['shared'] == 'PASS' and s['n_scoped'] > 0 and s['n_fail'] == s['n_scoped'],
    # Unqualified forms, kept so that removing the qualification is refused on meaning, not on spelling
    'a nonempty in-scope set whose candidates all fail a check': lambda s: s['n_scoped'] > 0 and s['n_fail'] == s['n_scoped'],
    'at least one candidate is in scope and every in-scope candidate fails a check':
        lambda s: s['n_scoped'] > 0 and s['n_fail'] == s['n_scoped'],
    'the shared safety state is unresolved': lambda s: s['shared'] in ('UNKNOWN', 'CONFLICTING'),
    'no record is in scope': lambda s: s['n_scoped'] == 0,
    'no candidate is ready or waiting while at least one is unresolved':
        lambda s: s['n_ready'] == 0 and s['n_wait'] == 0 and s['n_unres'] > 0,
    'no candidate is ready, and at least one fully supported candidate is on a busy robot':
        lambda s: s['n_ready'] == 0 and s['n_wait'] > 0,
    'several candidates remain after any complete declared order': lambda s: s['n_remaining'] > 1,
    'a choice is stale or names an excluded candidate': lambda s: s['choice'] in ('stale', 'excluded'),
    'one candidate remains, it requires confirmation, and no matching confirmation was given':
        lambda s: s['n_remaining'] == 1 and s['conf_required'] and not s['confirmed'],
    'one candidate remains, confirmed against the current digest or not requiring confirmation':
        lambda s: s['n_remaining'] == 1 and (s['confirmed'] or not s['conf_required']),
}
NOTES = {'an offline recommendation, not release authority'}


def table2(text):
    i = text.index('*Table 2.'); j = text.index('\n\n', text.index('| Outcome |', i))
    rows = [[c.strip() for c in l.strip().strip('|').split('|')] for l in text[i:j].splitlines() if l.startswith('|')]
    return [(r[0], r[1]) for r in rows[2:]]


def clauses(cond):
    out = []
    for part in re.split(r';\s*', cond):
        part = re.sub(r'^or\s+', '', part.strip())
        if part in VOCAB or part in NOTES:
            out.append(part); continue
        for sub in re.split(r',\s+or\s+', part):          # E.g. the ASK row's two alternatives
            out.append(re.sub(r'^or\s+', '', sub.strip()))
    return out


def table_outcome(rows, s):
    for outcome, cond in rows:
        if any(VOCAB[c](s) for c in clauses(cond) if c in VOCAB):
            return outcome
    return None


def states():
    """Concrete selector inputs with their abstract states (computed here from the inputs, not from the selector)."""
    out = []
    g1 = [copy.deepcopy(PROFILES[k]) for k in ('G1_A_7N', 'G1_B_7N', 'G1_C_7N', 'G1_D_7N')]
    for shared, scope, avail in itertools.product(('PASS', 'FAIL', 'UNKNOWN', 'CONFLICTING'), ('none', 'B', 'BC'),
                                                  ('AVAILABLE', 'BUSY', 'UNKNOWN')):
        scoped_ids = {'none': [], 'B': ['G1_B_7N'], 'BC': ['G1_B_7N', 'G1_C_7N']}[scope]
        for meas in itertools.product(('PASS', 'FAIL', 'UNKNOWN'), repeat=len(scoped_ids)):
            cands = copy.deepcopy(g1)
            for cid, m in zip(scoped_ids, meas):
                next(c for c in cands if c['id'] == cid)['checks']['measurement'] = m
            req = request_for(PROFILES['G1_B_7N'])
            req['allowed_targets'] = {'none': ['NEW'], 'B': ['B'], 'BC': ['B', 'C']}[scope]
            live = live_for(cands, 'PASS'); live['shared_safety'] = shared; live['platforms']['G1']['availability'] = avail
            base = dict(shared=shared, n_scoped=len(scoped_ids), conf_required=True)
            cls = [('fail' if m == 'FAIL' else 'unres' if m == 'UNKNOWN' or avail == 'UNKNOWN' else
                    'wait' if avail == 'BUSY' else 'ready') for m in meas]
            ready = [cid for cid, k in zip(scoped_ids, cls) if k == 'ready']
            base.update(n_fail=cls.count('fail'), n_unres=cls.count('unres'), n_wait=cls.count('wait'), n_ready=len(ready))
            decls = [('none', None, None)]
            if ready:
                decls += [('stale_choice', 'stale', None), ('excluded_choice', 'excluded', None)]
                if len(ready) == 1:
                    decls += [('valid_confirmation', None, 'valid'), ('stale_confirmation', None, 'stale')]
                else:
                    decls += [('valid_choice_B', 'valid', None), ('valid_choice_B_and_confirmation', 'valid', 'valid')]
            for name, choice, conf in decls:
                s = dict(base, declaration=name, choice=choice if choice != 'valid' else None, confirmed=conf == 'valid')
                remaining = ready if choice is None else (['G1_B_7N'] if choice == 'valid' else ready)
                s['n_remaining'] = len(remaining)
                out.append((s, req, cands, live, choice, conf, scoped_ids, ready))
    # UR5 family: a profile that needs no confirmation
    ur5 = copy.deepcopy(PROFILES['UR5_whiteboard_detection'])
    for shared, meas, avail in itertools.product(('PASS', 'FAIL', 'UNKNOWN', 'CONFLICTING'), ('PASS', 'FAIL', 'UNKNOWN'),
                                                 ('AVAILABLE', 'BUSY', 'UNKNOWN')):
        c = copy.deepcopy(ur5); c['checks']['measurement'] = meas
        req = request_for(ur5); live = live_for([c], 'PASS'); live['shared_safety'] = shared; live['platforms']['UR5']['availability'] = avail
        k = 'fail' if meas == 'FAIL' else 'unres' if meas == 'UNKNOWN' or avail == 'UNKNOWN' else 'wait' if avail == 'BUSY' else 'ready'
        s = dict(shared=shared, n_scoped=1, conf_required=False, n_fail=int(k == 'fail'), n_unres=int(k == 'unres'),
                 n_wait=int(k == 'wait'), n_ready=int(k == 'ready'), n_remaining=int(k == 'ready'), declaration='none',
                 choice=None, confirmed=False)
        out.append((s, req, [c], live, None, None, [c['id']], [c['id']] if k == 'ready' else []))
    return out


def actual(req, cands, live, choice, conf, ready):
    first = select(req, cands, live)
    binding = first['binding']; stale = ('0' if binding[0] != '0' else '1') + binding[1:]
    kw = {}
    if choice is not None:
        cid = {'valid': 'G1_B_7N', 'stale': ready[0], 'excluded': 'G1_A_7N'}[choice]   # A is out of scope in every family
        kw['choice'] = {'kind': 'choice', 'binding': stale if choice == 'stale' else binding, 'candidate_id': cid}
    if conf is not None:
        target = 'G1_B_7N' if choice == 'valid' else ready[0]
        kw['confirmation'] = {'kind': 'confirmation', 'binding': stale if conf == 'stale' else binding, 'candidate_id': target}
    return select(req, cands, live, **kw)['decision'] if kw else first['decision']


def run(rows):
    unknown = [c for _, cond in rows for c in clauses(cond) if c not in VOCAB and c not in NOTES]
    if unknown:
        return {'unknown_clauses': unknown, 'disagreements': None, 'cases': 0}
    dis, n = [], 0
    for s, req, cands, live, choice, conf, scoped, ready in STATES:
        n += 1
        t, a = table_outcome(rows, s), actual(req, cands, live, choice, conf, ready)
        if t != a:
            dis.append({k: v for k, v in s.items() if k in ('shared', 'n_scoped', 'n_fail', 'n_unres', 'n_wait', 'n_ready', 'declaration', 'conf_required')}
                       | {'table': t, 'selector': a})
    return {'unknown_clauses': [], 'disagreements': dis, 'cases': n}


STATES = states()
ms = (R21 / 'records/TABLE2.md').read_text(); rows = table2(ms)
main = run(rows)
# Self-test 1: remove the qualification from the REFUSE row -> must be refused on meaning.
unq = [(o, c.replace('shared safety PASS and a nonempty', 'a nonempty')) for o, c in rows]
removal = run(unq)
# The deliberately incorrect comparison table must also fail.
r20 = run(table2((R21 / 'records/TABLE2_OLD_REFUSAL_CONTROL.md').read_text()))
codex_cases = [d for d in (removal['disagreements'] or []) if d['shared'] in ('UNKNOWN', 'CONFLICTING') and d['n_fail'] == d['n_scoped'] > 0]
ok = (main['disagreements'] == [] and not main['unknown_clauses'] and removal['disagreements'] and codex_cases and r20['disagreements']
      and [o for o, _ in rows] == ['REFUSE', 'WITHHOLD', 'WAIT', 'ASK', 'CONFIRM', 'SELECT'])
record = {'table2_excerpt_sha256': hashlib.sha256((R21 / 'records/TABLE2.md').read_bytes()).hexdigest(),
          'selector_sha256': hashlib.sha256((REC / 'selector/selector.py').read_bytes()).hexdigest(),
          'printed_rows': rows, 'cases': main['cases'],
          'decisions_covered': sorted({actual(r, c, l, ch, cf, rd) for _, r, c, l, ch, cf, _, rd in STATES}),
          'disagreements': main['disagreements'], 'unknown_clauses': main['unknown_clauses'],
          'self_test_qualification_removed': {'refused': bool(removal['disagreements']), 'disagreements': len(removal['disagreements'] or []),
                                              'codex_mixed_cases_among_them': len(codex_cases), 'examples': (removal['disagreements'] or [])[:4]},
          'self_test_r20_table': {'refused': bool(r20['disagreements']), 'disagreements': len(r20['disagreements'] or [])},
          'ok': bool(ok)}
(R21 / 'verification/TABLE2_SEMANTICS.json').write_text(json.dumps(record, indent=1, ensure_ascii=False) + '\n')
print(json.dumps({k: record[k] for k in ('cases', 'decisions_covered', 'disagreements', 'self_test_qualification_removed', 'self_test_r20_table', 'ok')}, indent=1)[:2500])
sys.exit(0 if ok else 1)
