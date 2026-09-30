"""Reproduce the saved decision examples using the supplied catalogue.

The catalogue contains the authors' assessments of the evidence. This script
checks the saved software decisions. It does not reassess the source experiments."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
from selector import EVIDENCE, EVIDENCE_LEVELS, LIVE, digest, select
PACKET = Path(__file__).resolve().parents[1]
WIN = 'declared_4s_scored_window_800_samples'; SCOPE = 'one_recorded_run_not_a_future_guarantee'
DEF = {'force_rmse': ('normal_force_error', 'RMSE', WIN, 'right_active_pad', 'N'),
       'force_max': ('normal_force_error', 'maximum_absolute', WIN, 'right_active_pad', 'N'),
       'force_mean': ('normal_force', 'mean', WIN, 'right_active_pad', 'N'),
       'target': ('in_plane_target_error', 'single_selection', 'selection_stage_detector_frame', 'declared_target_marker', 'mm'),
       'service': ('moving_service', 'fraction_of_samples', WIN, 'task_rule_right_active_idle_left_maintained', 'percent'),
       'path': ('qualified_travel', 'path_length', WIN, 'right_active_pad', 'mm')}
def metric(key, value):
    q, st, w, side, unit = DEF[key]
    return {'quantity': q, 'statistic': st, 'window': w, 'side': side, 'unit': unit, 'scope': SCOPE, 'value': value}

def catalog():
    path = PACKET / 'analysis/CANDIDATE_MAPPING.json'
    expected = '89fdbfda7e9529fe965ac76e25ff256855f9223346d407e1206f4a657470aea0'
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('Frozen catalogue digest mismatch')
    return json.loads(path.read_text())['profiles']

def request_for(candidate):
    return {'id': 'example_' + candidate['id'], 'capability': candidate['capability'],
            'domain': candidate['domain'], 'context': candidate['context'],
            'allowed_targets': [candidate['target']], 'allowed_modes': [candidate['mode']],
            'accepted_evidence_levels': [candidate['evidence_level']],
            'force': {'minimum_n': 7.0, 'maximum_n': 7.0,
                      'basis': 'Example owner-prescribed setpoint, not a dirt-removal requirement'}
                     if candidate['capability'] == 'regulated_wipe' else None,
            'required_settings': deepcopy(candidate['settings']), 'metrics': [],
            'mode_priority': [], 'mode_priority_basis': '', 'candidate_priority': [], 'candidate_priority_basis': ''}


def live_for(candidates, state='UNKNOWN'):
    return {'snapshot_id': 'AUTHORED_OFFLINE_' + state, 'shared_safety': state,
            'platforms': {name: {'availability': 'AVAILABLE' if state == 'PASS' else 'UNKNOWN', 'safety': state}
                          for name in sorted({c['platform'] for c in candidates})},
            'candidates': {c['id']: {'safety': state, 'checks': {k: state for k in sorted(LIVE[c['capability']])}}
                           for c in candidates}}


def dump(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


def examples(profiles):
    out = []
    for c in profiles:
        r = request_for(c)
        for state in ('UNKNOWN', 'PASS'):
            l = live_for(profiles, state)
            first = select(r, profiles, l)
            confirmed = None
            if first['decision'] == 'CONFIRM':
                confirmed = select(r, profiles, l, confirmation={
                    'kind': 'confirmation', 'binding': first['binding'], 'candidate_id': first['configuration']['id']})
            out.append({'name': c['id'] + '_live_' + state, 'request': r, 'live': l, 'live_semantics':
                        'Unobserved now' if state == 'UNKNOWN' else 'Synthetic live assumptions for software check ONLY',
                        'first': first, 'after_synthetic_confirmation': confirmed})
    g1 = profiles[:4]
    multi = request_for(g1[0]); multi['allowed_targets'] = list('ABCD')
    for name, changes in [('no_preference', {}), ('declared_site_preference', {
            'candidate_priority': ['G1_D_7N', 'G1_B_7N', 'G1_C_7N', 'G1_A_7N'],
            'candidate_priority_basis': 'Illustrative task-owner site order; no efficiency/quality ranking'})]:
        r = deepcopy(multi); r.update(changes)
        out.append({'name': name, 'request': r, 'live_semantics': 'Synthetic PASS for software check ONLY',
                    'first': select(r, g1, live_for(g1, 'PASS'))})
    for name, change in [('untested_10N', {'force': {'minimum_n': 10.0, 'maximum_n': 10.0,
                                                     'basis': 'authored exact request'}}),
                         ('owner_range_6_to_8N', {'force': {'minimum_n': 6.0, 'maximum_n': 8.0,
                                                            'basis': 'authored owner-authorised setpoint range'}}),
                         ('physical_G1', {'domain': 'physical'}),
                         ('untested_site', {'allowed_targets': ['NEW_SITE']})]:
        r = request_for(g1[1]); r.update(change)
        out.append({'name': name, 'request': r, 'live_semantics': 'Synthetic PASS for software check ONLY',
                    'first': select(r, profiles, live_for(profiles, 'PASS'))})
    # The first metric request uses criteria fixed before the run. Other thresholds were
    # chosen after seeing the results to illustrate metric definitions, not cleaning requirements.
    def req(target_sites, bounds):
        r = deepcopy(multi); r['allowed_targets'] = list(target_sites); r['metrics'] = bounds; return r
    def named(r, name):
        r['id'] = 'example_' + name; return r
    def bound(key, comparison, value, **change):
        q, st, w, side, unit = DEF[key]
        b = {'quantity': q, 'statistic': st, 'window': w, 'side': side, 'unit': unit, 'scope': SCOPE, 'comparison': comparison, 'bound': value}
        b.update(change); return b
    frozen = [bound('service', 'value_at_least', 90.0), bound('path', 'value_at_least', 25.0), bound('target', 'value_at_most', 10.0)]
    for name, sites, bounds, basis in [
            ('frozen_criteria_A_to_D', 'ABCD', frozen, 'task criteria frozen before execution (service, qualified travel, target identity)'),
            ('post_hoc_D_rmse_le_0p1N', 'D', [bound('force_rmse', 'value_at_most', 0.1)], 'threshold chosen after outcomes were known'),
            ('post_hoc_D_max_le_0p3N', 'D', [bound('force_max', 'magnitude_at_most', 0.3)], 'threshold chosen after outcomes were known'),
            ('post_hoc_B_max_le_0p3N', 'B', [bound('force_max', 'magnitude_at_most', 0.3)], 'threshold chosen after outcomes were known'),
            ('post_hoc_A_max_le_0p3N', 'A', [bound('force_max', 'magnitude_at_most', 0.3)], 'threshold chosen after outcomes were known'),
            ('post_hoc_A_to_D_max_le_0p3N', 'ABCD', [bound('force_max', 'magnitude_at_most', 0.3)], 'threshold chosen after outcomes were known'),
            ('post_hoc_D_whole_job_rmse', 'D', [bound('force_rmse', 'value_at_most', 0.1, window='whole_48s_job')],
             'threshold chosen after outcomes were known; window not recorded')]:
        r = named(req(sites, bounds), name)
        out.append({'name': name, 'request': r, 'threshold_basis': basis, 'live_semantics': 'Synthetic PASS for software check ONLY',
                    'first': select(r, g1, live_for(g1, 'PASS'))})
    r = named(req('D', [bound('force_rmse', 'value_at_most', 0.1)]), 'post_hoc_D_rmse_live_unobserved')
    out.append({'name': 'post_hoc_D_rmse_live_unobserved', 'request': r, 'threshold_basis': 'threshold chosen after outcomes were known',
                'live_semantics': 'Unobserved now', 'first': select(r, g1, live_for(g1))})
    # Live-state variants over the four G1 configurations (synthetic).
    for name in ('local_hazard_at_B', 'G1_platform_busy', 'shared_hazard'):
        l = live_for(g1, 'PASS')
        if name == 'local_hazard_at_B':
            l['candidates']['G1_B_7N']['safety'] = 'FAIL'
        elif name == 'G1_platform_busy':
            l['platforms']['G1']['availability'] = 'BUSY'
        else:
            l['shared_safety'] = 'FAIL'
        out.append({'name': name, 'request': deepcopy(multi), 'live': l,
                    'live_semantics': 'Synthetic live assumptions for software check ONLY',
                    'first': select(multi, g1, l)})
    return out



def main():
    expected = json.loads((PACKET / 'analysis/DEVELOPMENT_EXAMPLES.json').read_text())
    got = examples(catalog())
    if got != expected['examples']:
        raise AssertionError('Development examples differ from the frozen record')
    print(json.dumps({'examples': len(got), 'exact_match': True,
                      'scope': 'offline software; frozen author-assessed inputs; no robot execution'}))

if __name__ == '__main__':
    main()
