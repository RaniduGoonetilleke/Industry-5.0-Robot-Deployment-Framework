"""Check the current catalogue and the human roles required by each mode.

The tests cover the UR5 attendant role and verify that the other profiles retain
their recorded settings and decisions. Live facts are synthetic test inputs.

Run from the package root: python3 -B -m unittest -v catalogue_r4/test_catalogue_r4.py"""
import hashlib, json, sys, unittest
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(ROOT/'selector'))
from selector import select, digest                 # noqa: E402
from build_examples import request_for, live_for    # noqa: E402

SELECTOR_SHA256 = '922cb93fa1265a8cc50987812d3c062345b99aa2b676536173a68f21eef22d1e'
FROZEN_SHA256 = '89fdbfda7e9529fe965ac76e25ff256855f9223346d407e1206f4a657470aea0'
R3_SHA256 = '7d84a94d4ba892dee324bf763092573b5dff1887aae95665fbe31f7579bb10ba'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
DOC = json.loads((HERE/'CANDIDATE_MAPPING_R4.json').read_text())
R4 = DOC['profiles']; ROLES = DOC['human_roles_by_mode']
R3 = json.loads((ROOT/'catalogue_r3/CANDIDATE_MAPPING_R3.json').read_text())['profiles']
BY4 = {p['id']: p for p in R4}; BY3 = {p['id']: p for p in R3}
UR5 = 'UR5_whiteboard_detection'; MODE = 'attended_start_automatic_detection'
RECORDS = {r['id']: r for r in json.loads((ROOT/'records/CASE_CAPABILITY_RECORDS.json').read_text())['records']}
CLARIFICATION = json.loads((ROOT/'records/UR5_OPERATION_CLARIFICATION.json').read_text())


def decide(request, catalogue, state):
    r = select(request, catalogue, live_for(catalogue, state))
    return r['decision'], r['configuration']['id'] if r['configuration'] else None, r['remaining']


class Catalogue(unittest.TestCase):
    def test_selector_and_historical_catalogues_are_unchanged(self):
        self.assertEqual(sha(ROOT/'selector/selector.py'), SELECTOR_SHA256)
        self.assertEqual(sha(ROOT/'analysis/CANDIDATE_MAPPING.json'), FROZEN_SHA256)
        self.assertEqual(sha(ROOT/'catalogue_r3/CANDIDATE_MAPPING_R3.json'), R3_SHA256)

    def test_only_the_ur5_profile_differs_from_r3(self):
        self.assertEqual([p['id'] for p in R4], [p['id'] for p in R3]); self.assertEqual(len(R4), 10)
        self.assertEqual([p['id'] for p in R4 if p != BY3[p['id']]], [UR5])
        new, old = BY4[UR5], BY3[UR5]
        self.assertEqual(sorted(k for k in new if new[k] != old[k]),
                         ['assessment_basis', 'evidence_ids', 'evidence_sha256', 'mode', 'qualifiers', 'source_pins'])
        self.assertEqual((old['mode'], new['mode']), ('reported_static_detection', MODE))
        for key in ('capability', 'domain', 'context', 'target', 'evidence_level', 'checks', 'settings', 'metrics',
                    'confirmation_required', 'force_setpoint_n', 'platform'):
            self.assertEqual(new[key], old[key], key)          # The detection evidence and its level are not strengthened
        self.assertEqual(new['evidence_level'], 'physical-report'); self.assertIs(new['confirmation_required'], False)

    def test_the_correction_names_its_source_and_its_limits(self):
        new, old = BY4[UR5], BY3[UR5]
        self.assertEqual(new['qualifiers'][:len(old['qualifiers'])], old['qualifiers'])      # Earlier qualifiers kept
        added = ' '.join(new['qualifiers'][len(old['qualifiers']):])
        for phrase in ("earlier team account", 'no trial log', 'no per-target confirmation', 'detection profile only',
                       'not registered as a wiping capability'):
            self.assertIn(phrase, added)
        self.assertIn('not reconstructed from trial logs or source code', new['assessment_basis'])
        self.assertEqual(new['evidence_ids'], ['UR5-01', 'UR5-03', 'UR5-15'])
        self.assertEqual(new['evidence_sha256'], digest(new['source_pins']))
        for pin, value in old['source_pins'].items():                                       # Historical pins carried
            self.assertEqual(new['source_pins'][pin], value)
        shipped = {pin: value for pin, value in new['source_pins'].items() if pin.startswith('records/')}
        self.assertEqual(sorted(shipped), ['records/CASE_CAPABILITY_RECORDS.json', 'records/UR5_OPERATION_CLARIFICATION.json'])
        for pin, value in shipped.items():                                                  # The new sources ship with the package
            self.assertEqual(sha(ROOT/pin), value, pin)

    def test_the_author_account_is_not_presented_as_a_verified_record(self):
        self.assertEqual(CLARIFICATION['status'], 'AUTHOR_ACCOUNT')
        self.assertEqual(len(CLARIFICATION['what_this_record_is_not']), 4)
        self.assertIs(CLARIFICATION['public_demonstration']['video_reviewed_in_this_packet'], False)
        self.assertTrue(CLARIFICATION['qualified_by_the_author'])                           # The uncertain detail stays qualified
        for rid in ('UR5-01', 'UR5-12', 'UR5-13'):
            self.assertEqual((RECORDS[rid]['status'], RECORDS[rid]['evidence_level']), ('AUTHOR_ACCOUNT', 'physical-author-account'), rid)
        self.assertEqual(RECORDS['UR5-08']['status'], 'REPORTED')
        # The report's limits are retained: no continuous tracking, no force control, no cleaning effectiveness
        self.assertEqual((RECORDS['UR5-09']['status'], RECORDS['UR5-09']['value']), ('REPORTED', 'not achieved'))
        self.assertEqual((RECORDS['UR5-11']['status'], RECORDS['UR5-17']['status']), ('NOT_EVALUATED', 'NOT_EVALUATED'))
        self.assertEqual(RECORDS['UR5-02']['status'], 'UNKNOWN')                            # Operator-state sensing stays unknown
        self.assertFalse(any(p['platform'] == 'UR5' and p['capability'] == 'regulated_wipe' for p in R4))


class HumanRoles(unittest.TestCase):
    def test_every_recorded_mode_declares_its_human_role(self):
        self.assertEqual(set(ROLES), {p['mode'] for p in R4})
        for p in R4:
            self.assertIs(ROLES[p['mode']]['per_target_confirmation'], p['confirmation_required'], p['id'])
        self.assertEqual({m: e['roles'] for m, e in ROLES.items()},
                         {'explicit_command_confirmation_STOP': ['operator'], 'virtual_confirmation_then_bounded_wipe': ['operator'],
                          MODE: ['attendant']})
        self.assertEqual(sorted(DOC['role_definitions']), ['attendant', 'confirmer', 'operator'])
        self.assertIn('no movement command and no per-target confirmation', DOC['role_definitions']['attendant'])
        self.assertIn("earlier team account", ROLES[MODE]['basis'])


class Decisions(unittest.TestCase):
    def test_the_selector_decisions_for_the_ur5_profile(self):
        own = request_for(BY4[UR5])
        self.assertEqual(decide(own, R4, 'PASS'), ('SELECT', UR5, [UR5]))                   # Synthetic passing live facts
        self.assertEqual(decide(own, R4, 'UNKNOWN')[0], 'WITHHOLD')                         # Live facts unobserved, as at present
        historical = select(request_for(BY3[UR5]), R4, live_for(R4, 'PASS'))                # A request in the historical mode label
        self.assertEqual((historical['decision'], historical['not_applicable'][UR5]), ('WITHHOLD', ['mode']))
        confirm_only = dict(own, allowed_modes=['explicit_command_confirmation_STOP'])
        self.assertEqual(decide(confirm_only, R4, 'PASS')[0], 'WITHHOLD')                   # No confirmed-detection record exists

    def test_every_other_decision_is_the_same_as_with_r3(self):
        for p in R4:
            if p['id'] == UR5:
                continue
            r = request_for(p)
            if p['capability'] == 'regulated_wipe':
                r['force'] = {'minimum_n': p['force_setpoint_n'], 'maximum_n': p['force_setpoint_n'], 'basis': 'test'}
            for state in ('PASS', 'UNKNOWN'):
                self.assertEqual(decide(r, R4, state), decide(r, R3, state), (p['id'], state))
        wipe = request_for(BY4['G1_B_8N'])
        for lo, hi in ((7., 7.), (8., 8.), (6., 8.), (7.5, 7.5), (7.5, 8.), (9., 9.)):      # The evidence-growth requests
            wipe['force'] = {'minimum_n': lo, 'maximum_n': hi, 'basis': 'test'}; wipe['required_settings'] = {}
            self.assertEqual(decide(wipe, R4, 'PASS'), decide(wipe, R3, 'PASS'), (lo, hi))

    def test_the_saved_record_matches_a_fresh_computation(self):
        saved = json.loads((HERE/'DECISIONS_R4.json').read_text())
        self.assertEqual(saved['catalogue_r4_sha256'], sha(HERE/'CANDIDATE_MAPPING_R4.json'))
        self.assertEqual((saved['profiles_changed'], len(saved['profiles_unchanged'])), ([UR5], 9))
        for state in ('PASS', 'UNKNOWN'):
            d = decide(request_for(BY4[UR5]), R4, state)
            self.assertEqual((saved['ur5_own_request']['r4'][state]['decision'], saved['ur5_own_request']['r4'][state]['configuration']), d[:2])
            self.assertEqual(saved['ur5_own_request']['r3'][state]['decision'], saved['ur5_own_request']['r4'][state]['decision'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
