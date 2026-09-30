"""Check the location-B reconstruction and its evidence boundaries.

Run from the package root: python3 -B -m unittest -v lifecycle_example/test_lifecycle_b.py"""
import contextlib, hashlib, io, json, sys, unittest
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import lifecycle_b                                  # noqa: E402

SELECTOR_SHA256 = '922cb93fa1265a8cc50987812d3c062345b99aa2b676536173a68f21eef22d1e'
REGISTER_SHA256 = '89fdbfda7e9529fe965ac76e25ff256855f9223346d407e1206f4a657470aea0'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
SAVED = json.loads((HERE/'LIFECYCLE_B.json').read_text())
S1 = SAVED['stage_1_before_the_first_run_at_B']
S2 = SAVED['stage_2_before_the_record_of_B']
S3 = SAVED['stage_3_after_review_and_recording']


class Lifecycle(unittest.TestCase):
    def test_the_saved_result_is_reproduced_from_unchanged_code(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            lifecycle_b.main()
        self.assertEqual(out.getvalue(), (HERE/'LIFECYCLE_B.json').read_text())
        self.assertEqual(sha(ROOT/'selector/selector.py'), SELECTOR_SHA256)
        self.assertEqual(sha(ROOT/'analysis/CANDIDATE_MAPPING.json'), REGISTER_SHA256)
        self.assertEqual(SAVED['sources']['selector_sha256'], SELECTOR_SHA256)
        self.assertEqual(SAVED['label'], 'RETROSPECTIVE_RECONSTRUCTION')

    def test_stage_1_is_test_admission_and_is_conditional_on_a_confirmation(self):
        self.assertEqual(S1['program'], 'historical/decision_prototype.py')
        self.assertEqual(S1['before_the_run']['action'], 'REQUEST_CONFIRMATION')
        self.assertEqual((S1['before_the_run']['failed'], S1['before_the_run']['unresolved']), ([], []))
        self.assertEqual(S1['at_selection']['action'], 'ELIGIBLE_WITHIN_DECLARED_SCOPE')
        self.assertEqual(S1['at_selection']['confirmation_events'], ['POINT', 'OKAY'])
        self.assertIn('not evidence that the task would succeed', S1['meaning'])

    def test_stage_2_withholds_because_no_record_at_B_is_in_the_register(self):
        self.assertNotIn('G1_B_7N', S2['profiles']); self.assertEqual(len(S2['profiles']), 5)
        for live in ('live_facts_unobserved', 'synthetic_passing_live_facts'):
            self.assertEqual((S2[live]['decision'], S2[live]['configuration'], S2[live]['remaining']), ('WITHHOLD', None, []), live)
            self.assertEqual(S2[live]['candidate_reasons'], {})                  # Nothing was in scope, so nothing was assessed
        self.assertIn('no record for the requested', S2['synthetic_passing_live_facts']['reason'])
        for other in ('G1_A_7N', 'G1_C_7N', 'G1_D_7N'):                          # A record at another location is no evidence at B
            self.assertEqual(S2['synthetic_passing_live_facts']['out_of_scope'][other], ['target'])
        self.assertNotIn('after_synthetic_confirmation', S2)

    def test_stage_3_supports_the_configuration_only_with_live_checks_and_a_fresh_confirmation(self):
        self.assertIn('G1_B_7N', S3['profiles']); self.assertEqual(len(S3['profiles']), 6)
        self.assertEqual(S3['live_facts_unobserved']['decision'], 'WITHHOLD')
        self.assertTrue(S3['live_facts_unobserved']['candidate_reasons']['G1_B_7N']['unresolved'])
        first = S3['synthetic_passing_live_facts']
        self.assertEqual((first['decision'], first['configuration']), ('CONFIRM', 'G1_B_7N'))
        self.assertEqual(S3['after_synthetic_confirmation']['decision'], 'SELECT')
        later = S3['same_confirmation_on_a_later_snapshot']                      # A recorded run gives no permanent permission
        self.assertEqual((later['decision'], later['configuration']), ('CONFIRM', 'G1_B_7N'))

    def test_a_register_made_before_the_second_run_gives_the_same_stage_2_answer(self):
        register = json.loads((ROOT/'analysis/CANDIDATE_MAPPING.json').read_text())['profiles']
        before_run_2 = [c for c in register if c['id'] not in ('G1_B_7N', 'G1_C_7N', 'G1_D_7N')]   # B, C and D ran together
        self.assertEqual(len(before_run_2), 3)
        got = lifecycle_b.deployment(before_run_2)
        self.assertEqual(got['synthetic_passing_live_facts']['decision'], 'WITHHOLD')
        self.assertEqual(got['synthetic_passing_live_facts']['out_of_scope']['G1_A_7N'], ['target'])


if __name__ == '__main__':
    unittest.main()
