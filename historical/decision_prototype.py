"""Offline reference implementation. No robot APIs, learned policy or launch authority.

Inputs are AUTHOR-ASSESSED facts, not facts discovered or validated by this code.
Demonstrations and invariants establish software semantics only.
"""
import itertools
import json
from pathlib import Path

STATES = {'PASS', 'FAIL', 'UNKNOWN', 'CONFLICTING'}
EVIDENCE_KEYS = {'capability', 'domain', 'context', 'measurement', 'mode'}
LIVE_KEYS = {'valid_input', 'visible_target', 'path', 'contact_authorized'}


def decide(facts, confirmation_required=True, confirmed=False):
    if set(facts) != EVIDENCE_KEYS | LIVE_KEYS:
        raise ValueError('Missing or extra required checks')
    if any(type(v) is not str or v not in STATES for v in facts.values()):
        raise ValueError('Invalid fact state')
    if type(confirmation_required) is not bool or type(confirmed) is not bool:
        raise ValueError('Confirmation values must be booleans')
    checks = {name: facts[name] for name in sorted(facts)}
    failed = [name for name, state in checks.items() if state == 'FAIL']
    unresolved = [name for name, state in checks.items() if state in {'UNKNOWN', 'CONFLICTING'}]
    if failed:
        action = 'REFUSE_CURRENT_STATE'
    elif unresolved:
        action = 'WITHHOLD_UNSUPPORTED'
    elif confirmation_required and not confirmed:
        action = 'REQUEST_CONFIRMATION'
    else:
        action = 'ELIGIBLE_WITHIN_DECLARED_SCOPE'
    return {'action': action, 'checks': checks, 'failed': failed, 'unresolved': unresolved,
            'confirmation_required': confirmation_required, 'confirmed': confirmed,
            'release_authority': False}


def reference(facts, required, confirmed):
    """Competent flat all-checks reference, same validated input domain."""
    values = tuple(facts.values())
    if 'FAIL' in values:
        return 'REFUSE_CURRENT_STATE'
    if any(v != 'PASS' for v in values):
        return 'WITHHOLD_UNSUPPORTED'
    if required and not confirmed:
        return 'REQUEST_CONFIRMATION'
    return 'ELIGIBLE_WITHIN_DECLARED_SCOPE'


def choose(candidate_results):
    eligible = sorted(name for name, r in candidate_results.items()
                      if r['action'] == 'ELIGIBLE_WITHIN_DECLARED_SCOPE')
    if len(eligible) == 1:
        action = 'ONE_ELIGIBLE'
    elif eligible:
        action = 'MULTIPLE_ELIGIBLE'
    else:
        action = 'NO_ELIGIBLE_CANDIDATE'
    return {'action': action, 'eligible': eligible, 'candidates': candidate_results,
            'ranking': None, 'release_authority': False}


def main():
    here = Path(__file__).resolve().parent
    nominal = {key: 'PASS' for key in EVIDENCE_KEYS | LIVE_KEYS}
    examples = [
        ('g1_sim_nominal_authored', {}, True, True),
        ('g1_sim_confirmation_pending', {}, True, False),
        ('g1_sim_hidden_target', {'visible_target': 'FAIL'}, True, True),
        ('g1_requested_physical_with_sim_only_evidence', {'domain': 'UNKNOWN'}, True, True),
        ('g1_physical_even_if_human_watches', {'domain': 'UNKNOWN', 'mode': 'UNKNOWN'}, True, True),
        ('g1_unmeasured_force_setting', {'measurement': 'UNKNOWN', 'context': 'UNKNOWN'}, True, True),
        ('ur3_organic_fatigue_control_request', {'measurement': 'CONFLICTING', 'context': 'UNKNOWN'}, False, True),
        ('ur5_dynamic_glass_cleaning_request', {'context': 'UNKNOWN', 'measurement': 'UNKNOWN'}, False, True),
        ('known_bad_path_and_missing_evidence', {'path': 'FAIL', 'measurement': 'UNKNOWN'}, True, False),
    ]
    demo = {name: decide(dict(nominal, **change), required, confirmed)
            for name, change, required, confirmed in examples}
    total = 0
    # Complete finite input-state enumeration, not empirical samples.
    keys = sorted(nominal)
    for states in itertools.product(sorted(STATES), repeat=len(keys)):
        facts = dict(zip(keys, states))
        for required, confirmed in itertools.product([False, True], repeat=2):
            result = decide(facts, required, confirmed)
            assert result['action'] == reference(facts, required, confirmed)
            assert result['release_authority'] is False
            if result['action'] == 'ELIGIBLE_WITHIN_DECLARED_SCOPE':
                assert all(v == 'PASS' for v in states)
            total += 1
    # Confirmation must not remove any failure or evidence gap.
    for field in keys:
        for bad in ['FAIL', 'UNKNOWN', 'CONFLICTING']:
            facts = dict(nominal, **{field: bad})
            assert decide(facts, True, False)['action'] == decide(facts, True, True)['action']
    invalid = [dict(nominal, mode='pass'), dict(nominal, mode=True),
               {k:v for k,v in nominal.items() if k != 'domain'},
               dict(nominal, future_force_score='PASS')]
    for value in invalid:
        try:
            decide(value)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid input accepted')
    good = decide(nominal, True, True)
    mixed = choose({'a': good, 'b': decide(dict(nominal, domain='UNKNOWN'), True, True)})
    assert mixed['eligible'] == ['a']
    tie = choose({'a': good, 'b': good})
    assert tie['action'] == 'MULTIPLE_ELIGIBLE' and tie['ranking'] is None
    out = {
        'status': 'SOFTWARE_CHECKS_PASS',
        'scope': 'Authored demonstrations and finite logical input enumeration; not native trials, decision accuracy, or novelty evaluation.',
        'examples': demo, 'multiple_candidate_control': tie, 'unknown_candidate_control': mixed,
        'logical_combinations_compared': total,
        'differences_from_competent_all_checks_reference': 0,
        'interpretation': 'The layer structure changes no action relative to the same competent conjunction over this complete abstract input domain. No incremental decision benefit is established.',
        'facts_are_source_verified_by_this_code': False,
        'invalid_input_controls': len(invalid),
        'real_robot_connection': False,
    }
    (here / 'PROTOTYPE_RESULTS.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps({k:v for k,v in out.items() if k not in ['examples','multiple_candidate_control','unknown_candidate_control']}, indent=2))


if __name__ == '__main__':
    main()
