"""Check the four 8 N records, their measured values and the resulting decisions.

Live facts in these tests are synthetic. Source files outside this distribution
are listed in PUBLIC_OMISSIONS.json and reported as unavailable for checking.

Run from the package root: python3 -B -m unittest -v catalogue_r3/test_catalogue_r3.py"""
import copy, hashlib, json, random, sys, unittest
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(ROOT/'selector'))
from selector import select, digest                 # noqa: E402
from build_examples import request_for, live_for    # noqa: E402

SELECTOR_SHA256 = '922cb93fa1265a8cc50987812d3c062345b99aa2b676536173a68f21eef22d1e'
FROZEN_SHA256 = '89fdbfda7e9529fe965ac76e25ff256855f9223346d407e1206f4a657470aea0'
FROZEN_PATH = ROOT/'analysis/CANDIDATE_MAPPING.json'
R3 = json.loads((HERE/'CANDIDATE_MAPPING_R3.json').read_text())['profiles']
FROZEN = json.loads(FROZEN_PATH.read_text())['profiles']
RECORDS = json.loads((HERE/'FORCE_8N_RECORDS.json').read_text())
BY = {p['id']: p for p in R3}
SITES = 'ABCD'
# Map recorded source names to their package locations.
LOCAL = {'point_force_first_group_r1_results_review_20260927/': HERE/'source',
         'point_force_first_group_r1_release_20260927/point_force_first_group_r1/': ROOT/'native_sources/force'}


def local(pin):
    prefix = next(p for p in LOCAL if pin.startswith(p))
    return LOCAL[prefix]/pin[len(prefix):]


# PUBLIC_OMISSIONS.json lists source files held outside this distribution.
# Report these files as unavailable and verify every other source.
# The declared omissions must match the missing files exactly.

OMISSIONS_PATH = HERE/'PUBLIC_OMISSIONS.json'
OMITTED = json.loads(OMISSIONS_PATH.read_text())['omitted_pins'] if OMISSIONS_PATH.exists() else {}


def req(sites, lo, hi, **change):
    r = request_for(BY['G1_D_8N']); r.update(allowed_targets=list(sites), force={'minimum_n': lo, 'maximum_n': hi, 'basis': 'test'}, id='t')
    r['required_settings'] = {}; r.update(change); return r


def value(profile, quantity, statistic):
    return next(m['value'] for m in profile['metrics'] if (m['quantity'], m['statistic']) == (quantity, statistic))


class Catalogue(unittest.TestCase):
    def test_frozen_profiles_and_selector_unchanged(self):
        self.assertEqual(hashlib.sha256(FROZEN_PATH.read_bytes()).hexdigest(), FROZEN_SHA256)
        self.assertEqual(hashlib.sha256((ROOT/'selector/selector.py').read_bytes()).hexdigest(), SELECTOR_SHA256)
        self.assertEqual(R3[:6], FROZEN)
        self.assertEqual([p['id'] for p in R3[6:]], ['G1_%s_8N' % s for s in SITES])

    def test_new_profiles_are_exact_site_8N_twins_of_the_7N_profiles(self):
        for s in SITES:
            p8, p7 = BY['G1_%s_8N' % s], BY['G1_%s_7N' % s]
            self.assertEqual(p8['force_setpoint_n'], 8.0); self.assertEqual(p8['target'], s)
            for key in ('capability', 'domain', 'context', 'mode', 'platform', 'evidence_level', 'settings', 'confirmation_required'):
                self.assertEqual(p8[key], p7[key], key)   # Tested configuration and preconditions preserved
            self.assertEqual(p8['evidence_sha256'], digest(p8['source_pins']))
            definitions = lambda p: {(m['quantity'], m['statistic'], m['window'], m['side'], m['unit'], m['scope']) for m in p['metrics']}
            self.assertEqual(definitions(p8), definitions(BY['G1_D_7N']))   # The complete 7 N definition set
            self.assertLessEqual(definitions(p7), definitions(p8))            # A's 7 N record has no windowed maximum; its 8 N record does
            rec = RECORDS['sites'][s]
            self.assertEqual(value(p8, 'normal_force', 'mean'), rec['W1_right']['mean_n'])
            self.assertEqual(value(p8, 'normal_force_error', 'RMSE'), rec['W1_right']['rms_error_n'])
            self.assertEqual(value(p8, 'normal_force_error', 'maximum_absolute'), rec['W1_right']['maximum_abs_error_n'])
            self.assertIn('whole-circle (W2) right-pad force', ' '.join(p8['qualifiers']))
            self.assertIn('5 ms only', ' '.join(p8['qualifiers']))
            for pin, sha in p8['source_pins'].items():     # Every pinned source is shipped here with its pinned bytes, or declared omitted
                if pin in OMITTED:
                    self.assertFalse(local(pin).exists(), pin); self.assertEqual(OMITTED[pin], sha, pin)
                else:
                    self.assertEqual(hashlib.sha256(local(pin).read_bytes()).hexdigest(), sha, pin)
        self.assertIn('fix-development', ' '.join(BY['G1_A_8N']['qualifiers'])); self.assertIn('20 mm from A', ' '.join(BY['G1_C_8N']['qualifiers']))


    def test_recorded_values_match_the_results_review_table(self):
        table = {'A': (8.001030, 0.054793, 7.678736, 8.504552, 8.000896), 'B': (8.006605, 0.054481, 7.739632, 8.381366, 8.002603),
                 'C': (8.000905, 0.054920, 7.695996, 8.598704, 8.003407), 'D': (8.004308, 0.048600, 7.770627, 8.393905, 7.995686)}
        text = (HERE/'source/RESULTS_TABLE.md').read_text()
        for s, row in table.items():   # Six printed decimals in source/RESULTS_TABLE.md
            r = RECORDS['sites'][s]
            got = (r['W1_right']['mean_n'], r['W1_right']['rms_error_n'], r['W2_right_whole_circle']['minimum_n'],
                   r['W2_right_whole_circle']['maximum_n'], r['W3_right_final_hold']['mean_n'])
            for a, b in zip(got, row):
                self.assertLessEqual(abs(a-b), 5e-7, s)
                self.assertIn('%.6f' % b, text)


    def test_public_omissions_are_exactly_the_missing_pinned_files(self):
        pins = {pin: sha for p in R3[6:] for pin, sha in p['source_pins'].items()}
        missing = {pin for pin in pins if not local(pin).exists()}
        self.assertEqual(missing, set(OMITTED))
        if OMITTED:
            print('\nNOT VERIFIABLE IN THIS PUBLIC PACKAGE (pinned bytes held in the private archive): %d files' % len(OMITTED))


class Decisions(unittest.TestCase):
    live = live_for(R3, 'PASS')        # Synthetic: every live fact assumed to pass

    def test_exact_8N_at_each_tested_site_confirms_that_site_only(self):
        frozen_live = live_for(FROZEN, 'PASS')
        for s in SITES:
            self.assertEqual(select(req(s, 8., 8.), FROZEN, frozen_live)['decision'], 'WITHHOLD')   # No 8 N record was available in the earlier catalogue.
            first = select(req(s, 8., 8.), R3, self.live)
            self.assertEqual((first['decision'], first['configuration']['id']), ('CONFIRM', 'G1_%s_8N' % s))
            second = select(req(s, 8., 8.), R3, self.live, confirmation={'kind': 'confirmation', 'binding': first['binding'], 'candidate_id': 'G1_%s_8N' % s})
            self.assertEqual(second['decision'], 'SELECT')
        self.assertEqual(select(req(SITES, 8., 8.), R3, self.live)['decision'], 'ASK')

    def test_untested_setpoints_stay_unsupported(self):
        rng = random.Random(20260927)
        cases = [(7.5, 7.5), (7.2, 7.9), (9., 9.), (8.5, 11.), (6., 6.9)]+[(x, x) for x in (rng.uniform(7.0001, 7.9999) for _ in range(200))]
        for lo, hi in cases:
            for s in SITES:
                r = select(req(s, lo, hi), R3, self.live)
                self.assertEqual(r['decision'], 'WITHHOLD', (lo, hi, s))
                self.assertEqual(r['not_applicable']['G1_%s_8N' % s], ['setpoint']); self.assertEqual(r['not_applicable']['G1_%s_7N' % s], ['setpoint'])

    def test_owner_range_6_to_8N_at_B_is_the_one_changed_development_example(self):
        before = select(req('B', 6., 8.), FROZEN, live_for(FROZEN, 'PASS'))
        after = select(req('B', 6., 8.), R3, self.live)
        self.assertEqual((before['decision'], before['configuration']['id']), ('CONFIRM', 'G1_B_7N'))   # Analysis/DEVELOPMENT_EXAMPLES.json
        self.assertEqual((after['decision'], after['remaining']), ('ASK', ['G1_B_7N', 'G1_B_8N']))       # Table 6
        saved = json.loads((HERE/'DECISIONS_R3.json').read_text())['prior_examples_changed']
        self.assertEqual([(e['example'], e['before'], e['after'], e['after_remaining']) for e in saved],
                         [('owner_range_6_to_8N', 'CONFIRM', 'ASK', ['G1_B_7N', 'G1_B_8N'])])

    def test_each_profiles_own_request_is_withheld_while_live_facts_are_unobserved(self):
        unobserved = live_for(R3)
        for p in R3:
            r = request_for(p)
            if p['capability'] == 'regulated_wipe':
                r['force'] = {'minimum_n': p['force_setpoint_n'], 'maximum_n': p['force_setpoint_n'], 'basis': 'test'}
            self.assertEqual(select(r, R3, unobserved)['decision'], 'WITHHOLD', p['id'])
        self.assertEqual(len(R3), 10)

    def test_a_range_containing_both_settings_asks_unless_an_order_is_declared(self):
        r = select(req('D', 6., 9.), R3, self.live)
        self.assertEqual((r['decision'], r['remaining']), ('ASK', ['G1_D_7N', 'G1_D_8N']))
        ordered = req('D', 6., 9., candidate_priority=['G1_D_8N', 'G1_D_7N'], candidate_priority_basis='illustrative owner order')
        r = select(ordered, R3, self.live)
        self.assertEqual((r['decision'], r['configuration']['id']), ('CONFIRM', 'G1_D_8N'))

    def test_exact_7N_requests_are_unchanged_by_the_new_records(self):
        frozen_live = live_for(FROZEN, 'PASS')
        for s in SITES:
            before, after = select(req(s, 7., 7.), FROZEN, frozen_live), select(req(s, 7., 7.), R3, self.live)
            self.assertEqual((before['decision'], before['configuration']['id']), (after['decision'], after['configuration']['id']))

    def test_other_scope_limits_still_apply_at_8N(self):
        for change in ({'domain': 'physical'}, {'allowed_targets': ['NEW_SITE']}, {'accepted_evidence_levels': ['physical-published']},
                       {'allowed_modes': ['autonomous_wipe']}):
            self.assertEqual(select(req('D', 8., 8., **change), R3, self.live)['decision'], 'WITHHOLD', change)
        hazard = copy.deepcopy(self.live); hazard['shared_safety'] = 'FAIL'
        self.assertEqual(select(req('D', 8., 8.), R3, hazard)['decision'], 'REFUSE')
        self.assertEqual(select(req('D', 8., 8.), R3, live_for(R3))['decision'], 'WITHHOLD')   # Live facts unobserved, as at present


if __name__ == '__main__':
    unittest.main(verbosity=2)
