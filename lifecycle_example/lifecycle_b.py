"""Reconstruct three questions about location B using the saved evidence.

First, could the declared test be admitted? Second, was a 7 N wipe supported
before B had a record? Third, what changed after that record was available?
The admission reference and selector were written after the experiment. This is
a retrospective reconstruction, and the framework did not control the run.
Live checks and confirmations in the deployment examples are synthetic inputs.

Run from the package root: python3 -B lifecycle_example/lifecycle_b.py"""
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'selector'))
sys.path.insert(0, str(ROOT / 'historical'))
from selector import select                     # noqa: E402
from build_examples import live_for             # noqa: E402
from decision_prototype import decide           # noqa: E402

REGISTER = ROOT / 'analysis/CANDIDATE_MAPPING.json'
TRACE = ROOT / 'records/DECISION_TRACE.json'
SITE, PROFILE = 'B', 'G1_B_7N'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def request(site=SITE):
    return {'id': 'owner_request_7_N_at_%s' % site, 'capability': 'regulated_wipe', 'domain': 'simulation',
            'context': 'supported_original_wall_point_pilot', 'allowed_targets': [site],
            'allowed_modes': ['virtual_confirmation_then_bounded_wipe'], 'accepted_evidence_levels': ['simulation'],
            'force': {'minimum_n': 7.0, 'maximum_n': 7.0,
                      'basis': 'Illustrative owner-permitted right-pad setpoint; not a cleaning requirement'},
            'required_settings': {}, 'metrics': [], 'mode_priority': [], 'mode_priority_basis': '',
            'candidate_priority': [], 'candidate_priority_basis': ''}


def brief(r):
    return {'decision': r['decision'], 'configuration': r['configuration']['id'] if r['configuration'] else None,
            'remaining': r['remaining'], 'reason': r['reasons'][-1], 'out_of_scope': r['not_applicable'],
            'candidate_reasons': r['candidate_reasons']}


def admission():
    row = [x for x in json.loads(TRACE.read_text())['part_A_g1_locations'] if x['tag'] == SITE]
    assert len(row) == 1
    row = row[0]
    before = decide(row['pre_run_only']['facts'], True, False)
    at_selection = decide(row['facts'], True, row['confirmed'])
    assert before['action'] == row['pre_run_only']['action'] and at_selection['action'] == row['framework_action']
    return {'question': 'May the declared simulation test job run at B? (test admission)',
            'program': 'historical/decision_prototype.py', 'run': row['run'],
            'before_the_run': {'action': before['action'], 'failed': before['failed'], 'unresolved': before['unresolved'],
                               'facts': before['checks']},
            'at_selection': {'action': at_selection['action'], 'confirmed': row['confirmed'],
                             'confirmation_events': row['inputs']['confirmation_events']},
            'admission_inputs_rest_on': sorted({v['stage'] for v in row['evidence_basis'].values()}),
            'observed_outcome_joined_afterwards': row['observed_outcome'],
            'reconstructed': 'The reference program was written after this run, which its reviewed release admitted.',
            'meaning': 'Permission to run a test that collects evidence; not evidence that the task would succeed.'}


def deployment(catalogue):
    req = request()
    unobserved = select(req, catalogue, live_for(catalogue, 'UNKNOWN'))
    live = live_for(catalogue, 'PASS')
    first = select(req, catalogue, live)
    out = {'live_facts_unobserved': brief(unobserved), 'synthetic_passing_live_facts': brief(first)}
    if first['decision'] == 'CONFIRM':
        declared = {'kind': 'confirmation', 'binding': first['binding'], 'candidate_id': first['configuration']['id']}
        out['after_synthetic_confirmation'] = brief(select(req, catalogue, live, confirmation=declared))
        later = dict(live, snapshot_id='AUTHORED_OFFLINE_PASS_LATER')
        out['same_confirmation_on_a_later_snapshot'] = brief(select(req, catalogue, later, confirmation=declared))
    return out


def main():
    register = json.loads(REGISTER.read_text())['profiles']
    without = [c for c in register if c['id'] != PROFILE]
    assert len(without) == len(register) - 1
    result = {
        'label': 'RETROSPECTIVE_RECONSTRUCTION',
        'scope': ('Reconstructed after the runs. The framework did not control the experiment. The reference program and the '
                  'selector were both written after the run that tested B. The run was admitted by its reviewed release, so '
                  'stage 1 reconstructs the admission checks and stage 2 was never an actual decision. Live facts and '
                  'confirmations in stages 2 and 3 are synthetic software inputs.'),
        'sources': {'register': {'file': str(REGISTER.relative_to(ROOT)), 'sha256': sha(REGISTER),
                                 'profiles': [c['id'] for c in register]},
                    'trace': {'file': str(TRACE.relative_to(ROOT)), 'sha256': sha(TRACE)},
                    'selector_sha256': sha(ROOT / 'selector/selector.py'),
                    'reference_program_sha256': sha(ROOT / 'historical/decision_prototype.py')},
        'request': request(),
        'stage_1_before_the_first_run_at_B': admission(),
        'stage_2_before_the_record_of_B': {
            'question': 'Is a 7 N wipe at B a supported deployment?', 'program': 'selector/selector.py',
            'register': 'the six-profile register with %s removed' % PROFILE, 'profiles': [c['id'] for c in without],
            **deployment(without)},
        'stage_3_after_review_and_recording': {
            'question': 'Is a 7 N wipe at B a supported deployment?', 'program': 'selector/selector.py',
            'register': 'the six-profile register', 'profiles': [c['id'] for c in register], **deployment(register)},
        'not_shown': ['a prospective prediction', 'a decision benefit', 'a permanent permission',
                      'that the framework designed or selected the experiment'],
    }
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
