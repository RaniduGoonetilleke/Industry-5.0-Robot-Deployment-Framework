"""Check selector invariants on 60,000 seeded synthetic cases.

An independent oracle checks every outcome, including asking and withholding.
The checks cover safety, evidence, availability, confirmation, scope and recorded
metric definitions. Synthetic variants change setpoints, settings and evidence
levels. Missing or ambiguous metrics remain unknown. These are software checks,
not robot trials or measured deployment outcomes.

Run from the package root after creating verification/:
python3 -B repro/fuzz_invariants.py verification/FUZZ_INVARIANTS.json
"""
import json
import random
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'selector'))
from selector import select            # noqa: E402
from build_examples import catalog, request_for, live_for   # noqa: E402

rng = random.Random(20260926)
CAT = catalog()
ST = ['PASS', 'PASS', 'PASS', 'FAIL', 'UNKNOWN', 'CONFLICTING']


def in_scope(r, c):
    if not (c['capability'] == r['capability'] and c['domain'] == r['domain'] and c['context'] == r['context']
            and c['target'] in r['allowed_targets'] and c['mode'] in r['allowed_modes']
            and c['evidence_level'] in r['accepted_evidence_levels']):
        return False
    if r['force'] is not None and not (r['force']['minimum_n'] <= c['force_setpoint_n'] <= r['force']['maximum_n']):
        return False
    return all(k in c['settings'] and type(c['settings'][k]) is type(v) and c['settings'][k] == v
               for k, v in r['required_settings'].items())


DEFN = ('quantity', 'statistic', 'window', 'side', 'unit', 'scope')


def metric_states(r, c):
    out = []
    for q in r['metrics']:
        found = [m for m in c['metrics'] if all(m[k] == q[k] for k in DEFN)]
        if len(found) != 1:
            out.append('UNKNOWN'); continue
        v, b = found[0]['value'], q['bound']
        ok = v <= b if q['comparison'] == 'value_at_most' else abs(v) <= b if q['comparison'] == 'magnitude_at_most' else v >= b
        out.append('PASS' if ok else 'FAIL')
    return out


def states(c, l, r):
    lc = l['candidates'][c['id']]; p = l['platforms'][c['platform']]
    return list(c['checks'].values()) + list(lc['checks'].values()) + [lc['safety'], p['safety']] + metric_states(r, c)


def metric_requests(base):
    reqs = []
    for _ in range(rng.randint(1, 2)):
        if base['metrics'] and rng.random() < .8:
            m = rng.choice(base['metrics']); d = {k: m[k] for k in DEFN}; v = m['value']
        else:
            d = {'quantity': 'moving_service', 'statistic': 'fraction_of_samples', 'window': 'whole_48s_job', 'side': 'task_rule',
                 'unit': 'percent', 'scope': 'one_recorded_run_not_a_future_guarantee'}; v = 90.0
        if rng.random() < .25:                        # a definition that differs in one field from the recorded one: missing
            k = rng.choice(DEFN); d = dict(d, **{k: d[k] + '_other'})
        comparison = rng.choice(['value_at_most', 'magnitude_at_most', 'value_at_least'])
        bound = abs(v) * rng.choice([0.5, 1.0, 1.0, 1.5])
        reqs.append(dict(d, comparison=comparison, bound=bound))
    return reqs


def supported(r, c, l):
    return in_scope(r, c) and all(v == 'PASS' for v in states(c, l, r))


def oracle(r, cs, l):
    if l['shared_safety'] == 'FAIL':
        return 'REFUSE', []
    if l['shared_safety'] != 'PASS':
        return 'WITHHOLD', []
    scoped = [c for c in cs if in_scope(r, c)]
    if not scoped:
        return 'WITHHOLD', []
    ready, waiting, unresolved = [], [], []
    for c in scoped:
        st = states(c, l, r); av = l['platforms'][c['platform']]['availability']
        if 'FAIL' in st:
            continue
        if any(v != 'PASS' for v in st) or av == 'UNKNOWN':
            unresolved.append(c)
        elif av == 'BUSY':
            waiting.append(c)
        else:
            ready.append(c)
    ids = lambda xs: sorted(c['id'] for c in xs)
    if len(ready) == 1:
        return ('CONFIRM' if ready[0]['confirmation_required'] else 'SELECT'), ids(ready)
    if ready:
        return 'ASK', ids(ready)
    if waiting:
        return 'WAIT', ids(waiting)
    if unresolved:
        return 'WITHHOLD', ids(unresolved)
    return 'REFUSE', []


def main():
    record = Path(sys.argv[1]); bad = []; counts = {}; with_metrics = 0; metric_counts = {}
    for _ in range(60000):
        cs = deepcopy(rng.sample(CAT, rng.randint(0, 6)))
        for k, base in enumerate(list(cs)):          # synthetic variants: another setpoint, setting or evidence level
            if base['capability'] == 'regulated_wipe' and rng.random() < .3:
                v = deepcopy(base); v['id'] += '_SYN%d' % k; axis = rng.choice(['setpoint', 'settings', 'level'])
                if axis == 'setpoint': v['force_setpoint_n'] = rng.choice([8.0, 10.0])
                elif axis == 'settings': v['settings']['timestep_s'] = 0.0025
                else: v['evidence_level'] = 'simulation-model offline screen (no physics)'
                cs.append(v)
        base = rng.choice(CAT); r = request_for(base)
        r['allowed_targets'] = sorted(set([base['target']] + rng.sample(list('ABCD') + ['registered_cube', 'taped_region', 'NEW'], rng.randint(0, 3))))
        if rng.random() < .15: r['allowed_modes'] = ['synthetic_untested_mode']
        if rng.random() < .15: r['accepted_evidence_levels'] = ['physical-published']
        if r['force'] is not None and rng.random() < .3:
            lo = rng.choice([6.0, 7.0, 8.0]); r['force'].update(minimum_n=lo, maximum_n=lo + rng.choice([0.0, 1.0, 2.0]))
        if rng.random() < .2: r['domain'] = 'physical' if r['domain'] == 'simulation' else 'simulation'
        if rng.random() < .4: r['metrics'] = metric_requests(base)
        for c in cs:
            for key in c['checks']:
                c['checks'][key] = rng.choice(ST) if rng.random() < .3 else 'PASS'
            if c['force_setpoint_n'] is not None and rng.random() < .2: c['force_setpoint_n'] = rng.choice([8.0, 10.0])
            if c['metrics'] and rng.random() < .3:
                k = rng.randrange(len(c['metrics'])); change = rng.choice(['drop', 'duplicate', 'shift'])
                if change == 'drop': del c['metrics'][k]
                elif change == 'duplicate': c['metrics'].append(dict(c['metrics'][k], value=c['metrics'][k]['value'] * 1.1))
                else: c['metrics'][k]['value'] *= rng.choice([0.5, 2.0, -1.0])
        l = live_for(cs, 'PASS'); l['shared_safety'] = rng.choice(['PASS'] * 4 + ['FAIL', 'UNKNOWN', 'CONFLICTING'])
        for p in l['platforms'].values():
            p['availability'] = rng.choice(['AVAILABLE'] * 3 + ['BUSY', 'UNKNOWN'])
            p['safety'] = rng.choice(ST) if rng.random() < .2 else 'PASS'
        for c in cs:
            lc = l['candidates'][c['id']]
            lc['safety'] = rng.choice(ST) if rng.random() < .3 else 'PASS'
            for key in lc['checks']: lc['checks'][key] = rng.choice(ST) if rng.random() < .3 else 'PASS'
        o = select(r, cs, l); counts[o['decision']] = counts.get(o['decision'], 0) + 1
        got = (o['decision'], o['remaining'] if o['decision'] not in ('CONFIRM', 'SELECT') else [o['configuration']['id']])
        if got != oracle(r, cs, l): bad.append(('I5', got[0], oracle(r, cs, l)[0]))
        if r['metrics']:
            with_metrics += 1
            for c in cs:
                if in_scope(r, c):
                    for v in metric_states(r, c): metric_counts[v] = metric_counts.get(v, 0) + 1
        byid = {c['id']: c for c in cs}
        if o['decision'] in ('SELECT', 'CONFIRM'):
            c = byid[o['configuration']['id']]
            if not (l['shared_safety'] == 'PASS' and supported(r, c, l) and l['platforms'][c['platform']]['availability'] == 'AVAILABLE'):
                bad.append(('I1', o['decision']))
            if o['decision'] == 'CONFIRM':
                o2 = select(r, cs, l, confirmation={'kind': 'confirmation', 'binding': o['binding'], 'candidate_id': c['id']})
                if o2['decision'] != 'SELECT': bad.append(('I1', 'confirm-then-select'))
        if o['decision'] == 'WAIT':
            for cid in o['remaining']:
                c = byid[cid]
                if not (supported(r, c, l) and l['platforms'][c['platform']]['availability'] == 'BUSY'): bad.append(('I2', cid))
        if o['decision'] == 'REFUSE' and l['shared_safety'] != 'FAIL':
            scoped = [c for c in cs if in_scope(r, c)]
            if not scoped or any('FAIL' not in states(c, l, r) for c in scoped): bad.append(('I3', 'refuse'))
        scoped = [c for c in cs if in_scope(r, c)]
        l2 = {'snapshot_id': l['snapshot_id'], 'shared_safety': l['shared_safety'],
              'platforms': {k: v for k, v in l['platforms'].items() if k in {c['platform'] for c in scoped}},
              'candidates': {c['id']: l['candidates'][c['id']] for c in scoped}}
        o3 = select(r, scoped, l2)
        if (o3['decision'], o3['remaining'], o3['reasons']) != (o['decision'], o['remaining'], o['reasons']): bad.append(('I4', o['decision'], o3['decision']))
    record.write_text(json.dumps({'cases': 60000, 'seed': 20260926, 'decisions': counts, 'violations': len(bad),
                                  'requests_with_metric_requirements': with_metrics, 'metric_outcomes': metric_counts,
                                  'first_violations': bad[:10]}, indent=2, sort_keys=True) + '\n')
    print('cases 60000 decisions', counts, 'violations', len(bad), bad[:5])
    if bad: raise SystemExit(1)


if __name__ == '__main__':
    main()
