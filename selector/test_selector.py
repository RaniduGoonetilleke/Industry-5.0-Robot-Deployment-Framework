"""Test the selector's input checks, decisions and recorded metrics.

The candidate variations and live facts used here are synthetic test inputs."""
from copy import deepcopy
import itertools
import json
import math
import unittest
from selector import EVIDENCE, LIVE, digest, select
from build_examples import catalog, request_for, live_for

G1_METRIC = {'quantity': 'normal_force_error', 'statistic': 'RMS', 'window': 'W1', 'side': 'right',
             'unit': 'N', 'scope': 'one_recorded_run'}


def requirement(bound, comparison='value_at_most', **changes):
    r = dict(G1_METRIC, bound=bound, comparison=comparison); r.update(changes); return r


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.c = [deepcopy(catalog()[1])]
        self.r = request_for(self.c[0]); self.l = live_for(self.c, 'PASS')

    def choose(self, **kw):
        return select(self.r, self.c, self.l, **kw)

    def token(self, out, kind='confirmation'):
        return {'kind': kind, 'binding': out['binding'], 'candidate_id': out['configuration']['id']}

    def run_ok(self, r=None, c=None, l=None, **kw):
        """Call select and treat an unexpected exception as a test failure, so that a mutant cannot pass or fail only by error."""
        try:
            return select(self.r if r is None else r, self.c if c is None else c, self.l if l is None else l, **kw)
        except Exception as error:   # noqa: BLE001
            self.fail('unexpected %s: %s' % (type(error).__name__, error))

    def use_sites(self, sites):
        by = {c['target']: c for c in catalog()[:4]}
        self.c = [deepcopy(by[s]) for s in sites]; self.r = request_for(self.c[0])
        self.r['allowed_targets'] = list(sites); self.l = live_for(self.c, 'PASS')

    
    def test_P01_confirmation_selects_exact_configuration_without_dispatch(self):
        initial = self.choose(); self.assertEqual(initial['decision'], 'CONFIRM')
        out = self.choose(confirmation=self.token(initial))
        self.assertEqual(out['decision'], 'SELECT')
        self.assertEqual(out['configuration']['force_setpoint_n'], 7.0)
        self.assertFalse(out['release_authority']); self.assertFalse(out['dispatch_implemented'])

    def test_P02_no_live_observation_cannot_select(self):
        self.l = live_for(self.c)
        self.assertEqual(self.choose()['decision'], 'WITHHOLD')

    def test_P03_all_evidence_and_live_failures_survive_confirmation(self):
        token = self.token(self.choose())
        checks = ([('evidence', k) for k in sorted(EVIDENCE)] + [('live', k) for k in sorted(LIVE['regulated_wipe'])]
                  + [('local', 'safety'), ('platform', 'safety')])
        for section, key in checks:
            for value, expected in [('FAIL', 'REFUSE'), ('UNKNOWN', 'WITHHOLD'), ('CONFLICTING', 'WITHHOLD')]:
                with self.subTest(section=section, key=key, value=value):
                    c, l = deepcopy(self.c), deepcopy(self.l)
                    target = {'evidence': c[0]['checks'], 'live': l['candidates'][c[0]['id']]['checks'],
                              'local': l['candidates'][c[0]['id']], 'platform': l['platforms']['G1']}[section]
                    target[key] = value
                    self.assertEqual(select(self.r, c, l, confirmation=token)['decision'], expected)

    def test_P04_shared_hazard_stops_every_candidate(self):
        self.use_sites('ABCD'); self.l['shared_safety'] = 'FAIL'
        out = self.choose(); self.assertEqual(out['decision'], 'REFUSE'); self.assertEqual(out['remaining'], [])
        self.l['shared_safety'] = 'UNKNOWN'; self.assertEqual(self.choose()['decision'], 'WITHHOLD')

    def test_P05_busy_platform_waits_for_all_its_configurations(self):
        # All four configurations use the same G1 robot.
        self.use_sites('ABCD'); self.l['platforms']['G1']['availability'] = 'BUSY'
        out = self.choose(); self.assertEqual(out['decision'], 'WAIT')
        self.assertEqual(out['remaining'], ['G1_A_7N', 'G1_B_7N', 'G1_C_7N', 'G1_D_7N'])

    def test_P06_local_hazard_does_not_block_other_candidates(self):
        self.use_sites('AB'); self.l['candidates']['G1_A_7N']['safety'] = 'FAIL'
        self.assertEqual(self.choose()['configuration']['target'], 'B')

    def test_P07_all_busy_wait(self):
        self.l['platforms']['G1']['availability'] = 'BUSY'
        self.assertEqual(self.choose()['decision'], 'WAIT')

    def test_P08_busy_but_unsupported_does_not_wait_as_if_supported(self):
        self.l['platforms']['G1']['availability'] = 'BUSY'
        self.c[0]['checks']['measurement'] = 'UNKNOWN'
        self.assertEqual(self.choose()['decision'], 'WITHHOLD')

    def test_P09_no_force_interpolation_or_downward_inheritance(self):
        self.c[0]['force_setpoint_n'] = 10.0
        self.r['force'].update(minimum_n=9.0, maximum_n=9.0)
        self.assertEqual(self.choose()['decision'], 'WITHHOLD')
        self.c.append(deepcopy(self.c[0])); self.c[1]['id'] += '_synthetic_8N'; self.c[1]['force_setpoint_n'] = 8.0
        self.l = live_for(self.c, 'PASS')
        self.assertEqual(self.choose()['decision'], 'WITHHOLD')

    def test_P10_force_range_can_select_a_tested_setpoint(self):
        self.r['force'].update(minimum_n=6.0, maximum_n=8.0)
        out = self.choose(); self.assertEqual(out['decision'], 'CONFIRM')
        self.assertEqual(out['configuration']['force_setpoint_n'], 7.0)

    def test_P11_missing_force_rejected_for_wipe(self):
        self.r['force'] = None
        with self.assertRaises(ValueError): self.choose()

    def test_P12_detection_uses_declared_not_applicable_force(self):
        self.c = [catalog()[-1]]; self.r = request_for(self.c[0]); self.l = live_for(self.c, 'PASS')
        self.assertEqual(self.choose()['decision'], 'SELECT')
        self.assertEqual(set(self.l['candidates'][self.c[0]['id']]['checks']), {'valid_input', 'visible_target'})
        self.r['force'] = {'minimum_n': 7.0, 'maximum_n': 7.0, 'basis': 'bad'}
        with self.assertRaises(ValueError): self.choose()

    def test_P13_handover_still_needs_path_and_contact_authorization(self):
        self.assertEqual(LIVE['gesture_handover'], {'valid_input', 'visible_target', 'path', 'contact_authorized'})
        self.c = [catalog()[-2]]; self.r = request_for(self.c[0])
        for key in ('path', 'contact_authorized'):
            with self.subTest(key=key):
                self.l = live_for(self.c, 'PASS'); self.l['candidates'][self.c[0]['id']]['checks'][key] = 'UNKNOWN'
                self.assertEqual(self.choose()['decision'], 'WITHHOLD')
                del self.l['candidates'][self.c[0]['id']]['checks'][key]
                with self.assertRaises(ValueError): self.choose()

    def test_P14_untested_requested_mode_withholds_as_in_r19(self):
        # A request for an untested mode is withheld.
        self.r['allowed_modes'] = ['fully_autonomous_no_confirmation']
        out = self.run_ok(); self.assertEqual(out['decision'], 'WITHHOLD')
        self.assertEqual(out['not_applicable'], {self.c[0]['id']: ['mode']})

    def test_P15_domain_context_transfer_and_untested_target_withheld(self):
        for field, value in (('domain', 'physical'), ('context', 'higher_wall'), ('allowed_targets', ['NEW'])):
            with self.subTest(field=field):
                r = deepcopy(self.r); r[field] = value
                out = self.run_ok(r); self.assertEqual(out['decision'], 'WITHHOLD')
                self.assertEqual(out['configuration'], None)

    def test_P16_setting_mismatch_not_silently_adjusted(self):
        self.r['required_settings']['timestep_s'] = 0.0025
        self.assertEqual(self.choose()['decision'], 'WITHHOLD')

    def test_P17_settings_type_drift_is_not_an_exact_match(self):
        self.c[0]['settings']['declared_test_field'] = 1
        self.r['required_settings']['declared_test_field'] = True
        self.assertEqual(self.choose()['decision'], 'WITHHOLD')

    def test_P18_empty_registry_is_missing_evidence_not_impossibility(self):
        self.c = []; self.l = live_for([], 'PASS'); self.assertEqual(self.choose()['decision'], 'WITHHOLD')

    def test_P19_choose_before_confirmation_and_recheck_after_choice(self):
        self.use_sites('AB'); out = self.choose(); self.assertEqual(out['decision'], 'ASK')
        choice = {'kind': 'choice', 'binding': out['binding'], 'candidate_id': 'G1_B_7N'}
        chosen = self.choose(choice=choice); self.assertEqual(chosen['decision'], 'CONFIRM')
        self.assertEqual(self.choose(choice=choice, confirmation=self.token(chosen))['decision'], 'SELECT')
        self.l['candidates']['G1_B_7N']['checks']['visible_target'] = 'FAIL'
        changed = self.choose(choice=choice, confirmation=self.token(chosen))
        self.assertNotEqual(changed['decision'], 'SELECT')

    def test_P20_confirmation_is_bound_to_changed_settings_target_or_evidence(self):
        for field in ['target', 'settings', 'evidence_ids', 'evidence_sha256', 'evidence_level', 'snapshot']:
            with self.subTest(field=field):
                c, l = deepcopy(self.c), deepcopy(self.l); token = self.token(self.choose())
                if field == 'target': c[0]['target'] = 'D'
                elif field == 'settings': c[0]['settings']['path'] = 'different'
                elif field == 'evidence_ids': c[0]['evidence_ids'] += ['different_assessment']
                elif field == 'evidence_sha256':
                    c[0]['source_pins'] = dict(c[0]['source_pins'], extra='a' * 64); c[0]['evidence_sha256'] = digest(c[0]['source_pins'])
                elif field == 'evidence_level': c[0]['evidence_level'] = 'simulation-model offline screen (no physics)'
                else: l['snapshot_id'] = 'new_snapshot'
                out = select(self.r, c, l, confirmation=token)
                self.assertNotEqual(out['decision'], 'SELECT')
                if field in ('evidence_ids', 'evidence_sha256', 'snapshot'):
                    self.assertEqual(out['decision'], 'CONFIRM')   # Still supported; only the old token stops binding

    def test_P21_candidate_input_order_cannot_break_ties(self):
        self.use_sites('ABCD'); expected = self.choose()
        for perm in itertools.permutations(self.c):
            self.assertEqual(select(self.r, list(perm), self.l), expected)

    def test_P22_only_explicit_complete_preference_resolves_tie(self):
        self.use_sites('AB'); self.r['candidate_priority'] = ['G1_B_7N']
        self.r['candidate_priority_basis'] = 'synthetic user preference'
        self.assertEqual(self.run_ok()['decision'], 'ASK')
        self.r['candidate_priority'].append('G1_A_7N')
        self.assertEqual(self.choose()['configuration']['target'], 'B')

    def test_P23_mode_preference_applies_only_when_explicit(self):
        self.use_sites('AB'); self.c[1]['mode'] = 'synthetic_other_mode'
        self.r['allowed_modes'].append('synthetic_other_mode')
        self.assertEqual(self.choose()['decision'], 'ASK')
        self.r['mode_priority'] = ['synthetic_other_mode', self.c[0]['mode']]
        self.r['mode_priority_basis'] = 'test-only owner preference'
        self.assertEqual(self.choose()['configuration']['target'], 'B')

    def test_P24_stale_choice_needs_new_choice(self):
        self.use_sites('AB'); out = self.choose()
        choice = {'kind': 'choice', 'binding': out['binding'], 'candidate_id': 'G1_A_7N'}
        self.l['snapshot_id'] = 'changed'
        self.assertEqual(self.choose(choice=choice)['decision'], 'ASK')

    def test_P25_preference_needs_named_basis(self):
        for field in ('candidate_priority', 'mode_priority'):
            with self.subTest(field=field):
                r = deepcopy(self.r); r[field] = [self.c[0]['id'] if field == 'candidate_priority' else self.c[0]['mode']]
                with self.assertRaises(ValueError): select(r, self.c, self.l)
                other = 'mode_priority_basis' if field == 'candidate_priority' else 'candidate_priority_basis'
                r[other] = 'basis given only for the other order'
                with self.assertRaises(ValueError): select(r, self.c, self.l)

    def test_P26_metrics_require_same_definition_not_similar_number(self):
        self.c[0]['metrics'] = [dict(G1_METRIC, value=0.05)]
        self.r['metrics'] = [requirement(0.1)]; self.assertEqual(self.choose()['decision'], 'CONFIRM')
        for key, replacement in [('statistic', 'maximum_absolute'), ('window', 'whole_run'),
                                 ('side', 'left'), ('unit', 'Pa'), ('scope', 'guaranteed_next_run')]:
            with self.subTest(key=key):
                r = deepcopy(self.r); r['metrics'][0][key] = replacement
                self.assertEqual(select(r, self.c, self.l)['decision'], 'WITHHOLD')

    def test_P27_known_metric_failure_refuses_and_duplicate_is_ambiguous(self):
        self.c[0]['metrics'] = [dict(G1_METRIC, value=0.2)]; self.r['metrics'] = [requirement(0.1)]
        self.assertEqual(self.choose()['decision'], 'REFUSE')
        self.c[0]['metrics'].append(dict(G1_METRIC, value=0.2)); self.assertEqual(self.choose()['decision'], 'WITHHOLD')

    def test_P28_malformed_fields_and_nonfinite_values_refused(self):
        for bad in [True, math.nan, math.inf, '7']:
            with self.subTest(bad=bad):
                r = deepcopy(self.r); r['force']['maximum_n'] = bad
                with self.assertRaises(ValueError): select(r, self.c, self.l)
        self.c[0]['checks']['measurement'] = True
        with self.assertRaises(ValueError): self.choose()

    def test_P29_missing_guard_cannot_default_to_pass(self):
        del self.l['candidates'][self.c[0]['id']]['checks']['path']
        with self.assertRaises(ValueError): self.choose()

    def test_P30_output_mutation_does_not_mutate_inputs(self):
        old = deepcopy([self.r, self.c, self.l]); out = self.choose()
        out['configuration']['settings']['path'] = 'tampered'
        self.assertEqual([self.r, self.c, self.l], old)

    
    def test_R1a_irrelevant_sites_do_not_turn_refuse_into_withhold(self):
        for sites in ('A', 'ABCD'):
            with self.subTest(sites=sites):
                self.use_sites(sites); self.r['allowed_targets'] = ['A']
                self.l['candidates']['G1_A_7N']['safety'] = 'FAIL'
                out = self.choose(); self.assertEqual(out['decision'], 'REFUSE')
                self.assertEqual(sorted(out['not_applicable']), sorted(set('G1_%s_7N' % s for s in sites) - {'G1_A_7N'}))

    def test_R1b_other_capabilities_do_not_turn_missing_evidence_into_refuse(self):
        det = request_for(catalog()[-1])
        for reg in ([], catalog()[:4], catalog()[:5]):
            with self.subTest(n=len(reg)):
                out = select(det, reg, live_for(reg, 'PASS'))
                self.assertEqual(out['decision'], 'WITHHOLD')
                self.assertTrue(all('capability' in v for v in out['not_applicable'].values()))
                self.assertEqual(sorted(out['not_applicable']), sorted(c['id'] for c in reg))

    def test_R1c_decision_is_invariant_to_adding_out_of_scope_candidates(self):
        extra = catalog()[4:]   # UR3 and UR5, both out of scope for a wipe request
        for mutate in (lambda l: None, lambda l: l['candidates']['G1_B_7N'].update(safety='FAIL'),
                       lambda l: l['candidates']['G1_B_7N']['checks'].update(path='UNKNOWN'),
                       lambda l: l['platforms']['G1'].update(availability='BUSY')):
            self.c = [deepcopy(catalog()[1])]; self.r = request_for(self.c[0]); base_l = live_for(self.c, 'PASS'); mutate(base_l)
            full = self.c + deepcopy(extra); full_l = live_for(full, 'PASS'); mutate(full_l)
            a = select(self.r, self.c, base_l); b = select(self.r, full, full_l)
            self.assertEqual((a['decision'], a['remaining'], a['reasons']), (b['decision'], b['remaining'], b['reasons']))

    
    def test_R2a_busy_platform_does_not_block_another_platform(self):
        other = deepcopy(catalog()[1]); other['id'] = 'SYNTHETIC_second_G1_B_7N'; other['platform'] = 'SYNTHETIC_second_G1'
        self.c = [deepcopy(catalog()[1]), other]; self.r = request_for(self.c[0]); self.l = live_for(self.c, 'PASS')
        self.l['platforms']['G1']['availability'] = 'BUSY'
        out = self.choose(); self.assertEqual(out['decision'], 'CONFIRM')
        self.assertEqual(out['configuration']['platform'], 'SYNTHETIC_second_G1')

    def test_R2b_platform_hazard_blocks_only_its_configurations(self):
        other = deepcopy(catalog()[1]); other['id'] = 'SYNTHETIC_second_G1_B_7N'; other['platform'] = 'SYNTHETIC_second_G1'
        self.c = [deepcopy(catalog()[1]), other]; self.r = request_for(self.c[0]); self.l = live_for(self.c, 'PASS')
        self.l['platforms']['G1']['safety'] = 'FAIL'
        out = self.run_ok(); self.assertEqual(out['decision'], 'CONFIRM')
        self.assertEqual(out['configuration']['platform'], 'SYNTHETIC_second_G1')
        self.use_sites('ABCD'); self.l['platforms']['G1']['safety'] = 'FAIL'
        self.assertEqual(self.choose()['decision'], 'REFUSE')

    def test_R2c_platform_inventory_and_unknown_availability(self):
        del self.l['platforms']['G1']
        with self.assertRaises(ValueError): self.choose()
        self.l = live_for(self.c, 'PASS'); self.l['platforms']['G1']['availability'] = 'UNKNOWN'
        self.assertEqual(self.choose()['decision'], 'WITHHOLD')
        self.l['candidates'][self.c[0]['id']]['availability'] = 'AVAILABLE'   # Old per-configuration field
        with self.assertRaises(ValueError): self.choose()

    
    def test_R3_signed_metric_needs_declared_comparison(self):
        self.c[0]['metrics'] = [dict(G1_METRIC, statistic='mean_signed', value=-0.5)]
        self.r['metrics'] = [requirement(0.1, 'magnitude_at_most', statistic='mean_signed')]
        self.assertEqual(self.choose()['decision'], 'REFUSE')
        self.r['metrics'] = [requirement(0.1, 'value_at_most', statistic='mean_signed')]
        self.assertEqual(self.choose()['decision'], 'CONFIRM')
        del self.r['metrics'][0]['comparison']
        with self.assertRaises(ValueError): self.choose()

    def test_R3b_lower_bound_for_service_fraction(self):
        service = {'quantity': 'moving_service_fraction', 'statistic': 'fraction_of_samples', 'window': 'declared_4s_circle',
                   'side': 'right', 'unit': 'percent', 'scope': 'one_recorded_run'}
        self.c[0]['metrics'] = [dict(service, value=99.125)]
        self.r['metrics'] = [dict(service, bound=90.0, comparison='value_at_least')]
        self.assertEqual(self.run_ok()['decision'], 'CONFIRM')
        self.r['metrics'][0]['bound'] = 99.125; self.assertEqual(self.run_ok()['decision'], 'CONFIRM')   # At the bound
        self.r['metrics'][0]['bound'] = 99.5; self.assertEqual(self.run_ok()['decision'], 'REFUSE')

    
    def test_R4_nested_settings_match_exactly_including_types(self):
        self.c[0]['settings']['pads'] = {'left_enabled': 1, 'force_n': 7}
        for required, expected in [({'left_enabled': True, 'force_n': 7}, 'WITHHOLD'),
                                   ({'left_enabled': 1, 'force_n': 7, 'untested_key': 0}, 'WITHHOLD'),   # Tested record lacks a required key
                                   ({'left_enabled': 1, 'force_n': 7.0}, 'WITHHOLD'),
                                   ({'left_enabled': 1}, 'WITHHOLD'),
                                   ({'left_enabled': 1, 'force_n': 7}, 'CONFIRM')]:
            with self.subTest(required=required):
                self.r['required_settings']['pads'] = required
                self.assertEqual(self.choose()['decision'], expected)

    
    def test_R5_unhashable_or_wrong_type_values_raise_value_error(self):
        cases = [lambda r, c, l: l.update(shared_safety=['PASS']),
                 lambda r, c, l: r.update(capability=['regulated_wipe']),
                 lambda r, c, l: c[0].update(capability={'x': 1}),
                 lambda r, c, l: l['platforms']['G1'].update(availability=['BUSY']),
                 lambda r, c, l: l['candidates'][c[0]['id']].update(safety={'s': 1}),
                 lambda r, c, l: c[0].update(evidence_level=['simulation']),
                 lambda r, c, l: r['metrics'].append(requirement(0.1, ['value_at_most']))]
        for k, mutate in enumerate(cases):
            with self.subTest(case=k):
                r, c, l = deepcopy(self.r), deepcopy(self.c), deepcopy(self.l); mutate(r, c, l)
                with self.assertRaises(Exception) as caught: select(r, c, l)
                self.assertIs(type(caught.exception), ValueError)

    
    def test_R6a_evidence_level_not_accepted_withholds(self):
        self.c = [catalog()[-1]]; self.r = request_for(self.c[0]); self.l = live_for(self.c, 'PASS')
        self.r['accepted_evidence_levels'] = ['physical-published']
        out = self.run_ok(); self.assertEqual(out['decision'], 'WITHHOLD')
        self.assertEqual(out['not_applicable'], {'UR5_whiteboard_detection': ['evidence_level']})
        self.r['accepted_evidence_levels'] = ['invented-level']
        with self.assertRaises(ValueError): self.choose()

    def test_R6b_profiles_carry_the_record_evidence_level(self):
        levels = {p['id']: p['evidence_level'] for p in catalog()}
        self.assertEqual(levels, {'G1_A_7N': 'simulation', 'G1_B_7N': 'simulation', 'G1_C_7N': 'simulation',
                                  'G1_D_7N': 'simulation', 'UR3_registered_handover': 'physical-published',
                                  'UR5_whiteboard_detection': 'physical-report'})

    
    def test_R7_candidate_reasons_reported_when_shared_gate_blocks(self):
        self.use_sites('AB'); self.l = live_for(self.c)          # Nothing observed now
        out = self.choose(); self.assertEqual(out['decision'], 'WITHHOLD')
        self.assertEqual(out['reasons'], ['shared safety unresolved'])
        self.assertEqual(sorted(out['candidate_reasons']), ['G1_A_7N', 'G1_B_7N'])
        for cid in ('G1_A_7N', 'G1_B_7N'):
            self.assertIn('live:visible_target', out['candidate_reasons'][cid]['unresolved'])
            self.assertIn('platform availability unknown', out['candidate_reasons'][cid]['unresolved'])

    
    def test_R9a_choice_and_confirmation_are_distinct_declarations(self):
        self.use_sites('AB'); out = self.choose()
        choice = {'kind': 'choice', 'binding': out['binding'], 'candidate_id': 'G1_B_7N'}
        as_confirmation = dict(choice, kind='confirmation')
        with self.assertRaises(ValueError): select(self.r, self.c, self.l, choice=choice, confirmation=choice)
        with self.assertRaises(ValueError): select(self.r, self.c, self.l, choice=as_confirmation)
        for bad in ({'binding': out['binding'], 'candidate_id': 'G1_B_7N'}, dict(choice, candidate_id=['G1_B_7N']),
                    dict(choice, binding='x'), dict(choice, candidate_id='')):
            for kw in ('choice', 'confirmation'):
                with self.subTest(bad=str(bad)[:40], kw=kw):
                    b = dict(bad, kind=kw) if 'kind' in bad else bad
                    with self.assertRaises(Exception) as caught: select(self.r, self.c, self.l, **{kw: b})
                    self.assertIs(type(caught.exception), ValueError)

    def test_R9b_confirmation_for_another_candidate_is_not_accepted(self):
        self.use_sites('AB'); self.r['allowed_targets'] = ['A']
        out = self.run_ok(); self.assertEqual(out['decision'], 'CONFIRM')
        wrong = {'kind': 'confirmation', 'binding': out['binding'], 'candidate_id': 'G1_B_7N'}
        self.assertEqual(self.run_ok(confirmation=wrong)['decision'], 'CONFIRM')

    def test_R9c_binding_covers_request_and_assessment_fields(self):
        token = self.token(self.run_ok())
        changes = [lambda r, c: r['force'].update(minimum_n=6.5), lambda r, c: r['metrics'].append(requirement(1.0)),
                   lambda r, c: r['required_settings'].pop('path'), lambda r, c: c[0].update(assessment_basis='other basis'),
                   lambda r, c: c[0]['qualifiers'].append('other'), lambda r, c: c[0].update(force_setpoint_n=7.0 + 1e-9)]
        for k, change in enumerate(changes):
            with self.subTest(change=k):
                r, c = deepcopy(self.r), deepcopy(self.c)
                if k == 1: c[0]['metrics'] = [dict(G1_METRIC, value=0.05)]
                change(r, c); l = live_for(c, 'PASS')
                self.assertNotEqual(self.run_ok(r, c, l, confirmation=token)['decision'], 'SELECT')

    def test_R9d_evidence_digest_must_match_its_pins_and_qualifiers_survive(self):
        self.c[0]['evidence_sha256'] = 'f' * 64
        with self.assertRaises(ValueError): self.choose()
        self.c = [deepcopy(catalog()[0])]; self.r = request_for(self.c[0]); self.l = live_for(self.c, 'PASS')
        out = self.run_ok(confirmation=self.token(self.run_ok()))
        self.assertEqual(out['decision'], 'SELECT'); self.assertIn('20 mm from C', out['configuration']['qualifiers'])

    def test_R9e_skipped_preference_is_traced(self):
        self.use_sites('AB'); self.r['candidate_priority'] = ['G1_A_7N', 'G1_B_7N']
        self.r['candidate_priority_basis'] = 'test-only order'; self.l['candidates']['G1_A_7N']['safety'] = 'FAIL'
        out = self.run_ok(); self.assertEqual(out['configuration']['target'], 'B')
        self.assertIn({'field': 'id', 'skipped': 'G1_A_7N', 'reason': 'failed'}, out['preference_trace'])

    def test_R9f_duplicate_ids_unknown_priority_and_live_superset_rejected(self):
        cases = [lambda r, c, l: c.append(deepcopy(c[0])),
                 lambda r, c, l: (r.update(candidate_priority=['NO_SUCH']), r.update(candidate_priority_basis='x')),
                 lambda r, c, l: l['candidates'].update(EXTRA=deepcopy(l['candidates'][c[0]['id']])),
                 lambda r, c, l: l['platforms'].update(EXTRA={'availability': 'AVAILABLE', 'safety': 'PASS'})]
        for k, mutate in enumerate(cases):
            with self.subTest(case=k):
                r, c, l = deepcopy(self.r), deepcopy(self.c), deepcopy(self.l); mutate(r, c, l)
                with self.assertRaises(Exception) as caught: select(r, c, l)
                self.assertIs(type(caught.exception), ValueError)

    def test_R9g_numbers_bases_and_force_metric_scope(self):
        cases = [lambda r, c, l: c[0].update(force_setpoint_n=True), lambda r, c, l: r['metrics'].append(requirement(True)),
                 lambda r, c, l: r['force'].update(maximum_n=10 ** 400), lambda r, c, l: r['force'].update(basis='  '),
                 lambda r, c, l: r['required_settings'].update(odd={1, 2}), lambda r, c, l: r['required_settings'].update(odd={1: 'a', 'b': 2})]
        for k, mutate in enumerate(cases):
            with self.subTest(case=k):
                r, c, l = deepcopy(self.r), deepcopy(self.c), deepcopy(self.l); mutate(r, c, l)
                with self.assertRaises(Exception) as caught: select(r, c, l)
                self.assertIs(type(caught.exception), ValueError)
        det = request_for(catalog()[-1]); det['metrics'] = [requirement(0.1)]
        with self.assertRaises(ValueError): select(det, [catalog()[-1]], live_for([catalog()[-1]], 'PASS'))

    def test_R9h_missing_setting_and_bound_equality(self):
        self.r['required_settings']['not_tested'] = 'x'
        self.assertEqual(self.run_ok()['decision'], 'WITHHOLD')
        del self.r['required_settings']['not_tested']
        self.c[0]['metrics'] = [dict(G1_METRIC, value=0.1)]; self.r['metrics'] = [requirement(0.1)]
        self.assertEqual(self.run_ok()['decision'], 'CONFIRM')          # At the bound is within it
        self.c[0]['settings']['zero'] = 0.0; self.r['required_settings']['zero'] = -0.0
        self.assertEqual(self.run_ok()['decision'], 'WITHHOLD')

    def test_R9i_wipe_request_never_selects_another_capability(self):
        for reg in ([catalog()[-1]], [catalog()[-2]], catalog()[4:]):
            with self.subTest(n=len(reg)):
                out = self.run_ok(self.r, reg, live_for(reg, 'PASS'))
                self.assertEqual(out['decision'], 'WITHHOLD'); self.assertIsNone(out['configuration'])

    
    def synthetic(self, base, suffix, **changes):
        c = deepcopy(base); c['id'] = base['id'] + '_SYNTHETIC_' + suffix; c.update(changes); return c

    def test_R10a_other_setpoint_setting_or_level_cannot_turn_refuse_into_withhold(self):
        c7 = deepcopy(catalog()[2]); c7['metrics'] = [dict(G1_METRIC, value=0.051807)]
        r = request_for(c7); r['metrics'] = [requirement(0.05)]
        variants = {'setpoint': self.synthetic(c7, '10N', force_setpoint_n=10.0),
                    'settings': self.synthetic(c7, '2p5ms', settings=dict(c7['settings'], timestep_s=0.0025)),
                    'evidence_level': self.synthetic(c7, 'screen', evidence_level='simulation-model offline screen (no physics)')}
        base = self.run_ok(r, [c7], live_for([c7], 'PASS')); self.assertEqual(base['decision'], 'REFUSE')
        for axis, extra in variants.items():
            with self.subTest(axis=axis):
                reg = [c7, extra]; out = self.run_ok(r, reg, live_for(reg, 'PASS'))
                self.assertEqual((out['decision'], out['remaining'], out['reasons']), (base['decision'], base['remaining'], base['reasons']))
                self.assertEqual(out['not_applicable'], {extra['id']: [axis]})

    def test_R10b_failing_record_elsewhere_cannot_turn_withhold_into_refuse(self):
        b7 = deepcopy(catalog()[1]); r = request_for(b7)
        failing = self.synthetic(b7, '10N', force_setpoint_n=10.0, checks=dict(b7['checks'], measurement='FAIL'))
        for reg in ([], [failing]):
            with self.subTest(n=len(reg)):
                out = self.run_ok(r, reg, live_for(reg, 'PASS')); self.assertEqual(out['decision'], 'WITHHOLD')

    def test_R10c_no_record_at_requested_parameters_withholds_whatever_the_live_facts(self):
        self.r['force'].update(minimum_n=10.0, maximum_n=10.0)
        for state in ('PASS', 'FAIL', 'UNKNOWN'):
            with self.subTest(state=state):
                l = live_for(self.c, 'PASS'); l['candidates'][self.c[0]['id']]['safety'] = state
                out = self.run_ok(l=l); self.assertEqual(out['decision'], 'WITHHOLD')
                self.assertEqual(out['not_applicable'], {self.c[0]['id']: ['setpoint']})

    
    def test_R11a_list_settings_match_in_length_and_order(self):
        self.c[0]['settings']['gains'] = [0.1, 0.2, 0.3]
        for required, expected in [([0.1, 0.2], 'WITHHOLD'), ([0.1, 0.3, 0.2], 'WITHHOLD'), ([0.1, 0.2, 0.3, 0.4], 'WITHHOLD'),
                                   ([0.1, 0.2, 0.3], 'CONFIRM')]:
            with self.subTest(required=required):
                self.r['required_settings']['gains'] = required
                self.assertEqual(self.run_ok()['decision'], expected)

    def test_R11b_any_changed_field_invalidates_an_old_confirmation(self):
        other = 'simulation-model offline screen (no physics)'
        # The base request already admits each candidate variant, so a candidate-only change stays in scope.
        self.r['force'].update(minimum_n=6.0, maximum_n=8.0); self.r['accepted_evidence_levels'].append(other)
        self.r['allowed_targets'].append('B2'); self.r['allowed_modes'].append('another_mode')
        self.r['mode_priority_basis'] = 'owner basis'; self.r['candidate_priority_basis'] = 'owner basis'
        self.c.append(deepcopy(catalog()[-1]))                     # An out-of-scope profile on another robot (UR5)
        self.l = live_for(self.c, 'PASS')
        base = self.run_ok(); self.assertEqual(base['decision'], 'CONFIRM'); token = self.token(base)
        request_changes = {
            'id': lambda r, c, l: r.update(id='another_task'),
            'allowed_targets': lambda r, c, l: r['allowed_targets'].append('A'),
            'allowed_modes': lambda r, c, l: r['allowed_modes'].append('third_mode'),
            'accepted_evidence_levels': lambda r, c, l: r['accepted_evidence_levels'].append('physical-report'),
            'force': lambda r, c, l: r['force'].update(minimum_n=6.5, maximum_n=7.5),
            'force_basis': lambda r, c, l: r['force'].update(basis='another owner basis'),
            'required_settings': lambda r, c, l: r['required_settings'].pop('path'),
            'metrics': lambda r, c, l: (c[0].update(metrics=[dict(G1_METRIC, value=0.05)]), r['metrics'].append(requirement(1.0))),
            'mode_priority': lambda r, c, l: r.update(mode_priority=[c[0]['mode']]),
            'mode_priority_basis': lambda r, c, l: r.update(mode_priority_basis='another basis'),
            'candidate_priority': lambda r, c, l: r.update(candidate_priority=[c[0]['id']]),
            'candidate_priority_basis': lambda r, c, l: r.update(candidate_priority_basis='another basis')}
        candidate_changes = {
            'evidence_level': lambda r, c, l: c[0].update(evidence_level=other),
            'qualifiers': lambda r, c, l: c[0]['qualifiers'].append('another qualifier'),
            'assessment_basis': lambda r, c, l: c[0].update(assessment_basis='another basis'),
            'source_pins': lambda r, c, l: (c[0]['source_pins'].update(extra='b' * 64), c[0].update(evidence_sha256=digest(c[0]['source_pins']))),
            'evidence_ids': lambda r, c, l: c[0]['evidence_ids'].append('G1-01'),
            'settings': lambda r, c, l: c[0]['settings'].update(untested_extra='x'),
            'metrics': lambda r, c, l: c[0]['metrics'].append(dict(G1_METRIC, value=0.05)),
            'force_setpoint_n': lambda r, c, l: c[0].update(force_setpoint_n=7.5),
            'mode': lambda r, c, l: c[0].update(mode='another_mode'),
            'target': lambda r, c, l: c[0].update(target='B2'),
            'platform': None,
            'domain_context': lambda r, c, l: (c[0].update(context='ctx2'), r.update(context='ctx2'))}
        live_changes = {
            'snapshot_id': lambda r, c, l: l.update(snapshot_id='obs-later'),
            'other_robot_availability': lambda r, c, l: l['platforms']['UR5'].update(availability='BUSY'),
            'other_candidate_live_check': lambda r, c, l: l['candidates']['UR5_whiteboard_detection']['checks'].update(visible_target='FAIL'),
            'shared_state_unchanged_but_other_safety': lambda r, c, l: l['platforms']['UR5'].update(safety='UNKNOWN'),
            'added_out_of_scope_candidate': None}
        for group in (request_changes, candidate_changes, live_changes):
            for name, change in group.items():
                with self.subTest(field=name):
                    r, c, l = deepcopy(self.r), deepcopy(self.c), deepcopy(self.l)
                    if name == 'platform':
                        c[0]['platform'] = 'G1_renamed'; l = live_for(c, 'PASS')
                    elif name == 'added_out_of_scope_candidate':
                        c.append(deepcopy(catalog()[-2])); l = live_for(c, 'PASS')
                    else:
                        change(r, c, l)
                    out = self.run_ok(r, c, l, confirmation=token)
                    self.assertNotEqual(out['decision'], 'SELECT')
                    if name not in ('id', 'domain_context', 'platform', 'added_out_of_scope_candidate'):
                        self.assertEqual(out['decision'], 'CONFIRM')   # Still supported: only the old declaration stops binding

    def test_R11c_trace_text_and_choice_reasons(self):
        self.use_sites('AB'); self.c[1]['mode'] = 'synthetic_other_mode'; self.r['allowed_modes'].append('synthetic_other_mode')
        self.r['mode_priority'] = ['synthetic_other_mode', self.c[0]['mode']]; self.r['mode_priority_basis'] = 'MODE BASIS'
        self.r['candidate_priority'] = ['G1_A_7N', 'G1_B_7N']; self.r['candidate_priority_basis'] = 'CANDIDATE BASIS'
        out = self.run_ok()
        self.assertIn({'field': 'mode', 'applied': True, 'basis': 'MODE BASIS'}, out['preference_trace'])
        self.assertIn({'field': 'id', 'applied': False, 'reason': 'single ready candidate'}, out['preference_trace'])
        self.use_sites('AB'); self.r['candidate_priority'] = ['G1_A_7N']; self.r['candidate_priority_basis'] = 'CANDIDATE BASIS'
        self.assertIn({'field': 'id', 'applied': False, 'reason': 'incomplete declared order'}, self.run_ok()['preference_trace'])
        self.r['candidate_priority'] = ['G1_B_7N', 'G1_A_7N']
        self.assertIn({'field': 'id', 'applied': True, 'basis': 'CANDIDATE BASIS'}, self.run_ok()['preference_trace'])
        self.use_sites('ABC'); self.r['allowed_targets'] = ['A', 'B']; self.r['candidate_priority'] = ['G1_C_7N', 'G1_B_7N', 'G1_A_7N']
        self.r['candidate_priority_basis'] = 'x'
        self.assertIn({'field': 'id', 'skipped': 'G1_C_7N', 'reason': 'not applicable'}, self.run_ok()['preference_trace'])
        self.use_sites('AB'); out = self.run_ok()
        stale = {'kind': 'choice', 'binding': '0' * 64, 'candidate_id': 'G1_A_7N'}
        self.assertEqual(self.run_ok(choice=stale)['reasons'], ['choice stale: made for another request, assessment or snapshot'])
        self.l['candidates']['G1_A_7N']['safety'] = 'FAIL'; out = self.run_ok()
        self.assertEqual(out['decision'], 'CONFIRM')
        excluded = {'kind': 'choice', 'binding': out['binding'], 'candidate_id': 'G1_A_7N'}
        self.assertEqual(self.run_ok(choice=excluded)['reasons'], ['choice names a configuration that is not among the remaining options'])

    def test_R11d_validation_only_guards(self):
        wipe = [lambda r, c, l: c[0].update(qualifiers='not a list'), lambda r, c, l: c[0].update(qualifiers=[1]),
                lambda r, c, l: (c[0].update(source_pins={}), c[0].update(evidence_sha256=digest({}))),
                lambda r, c, l: (c[0].update(source_pins={'x': 'nothex'}), c[0].update(evidence_sha256=digest({'x': 'nothex'}))),
                lambda r, c, l: r['force'].update(minimum_n=0.0), lambda r, c, l: r['force'].update(minimum_n=8.0, maximum_n=6.0),
                lambda r, c, l: c[0].update(force_setpoint_n=0.0), lambda r, c, l: c[0].update(force_setpoint_n=-7.0),
                lambda r, c, l: l.update(snapshot_id=''), lambda r, c, l: c[0].update(evidence_ids=[]),
                lambda r, c, l: r.update(candidate_priority_basis=None), lambda r, c, l: r.update(mode_priority=['not_allowed_mode'], mode_priority_basis='x'),
                lambda r, c, l: r['metrics'].append(requirement(-0.1, 'magnitude_at_most')),
                lambda r, c, l: r['required_settings'].update(deep=__import__('functools').reduce(lambda a, _: [a], range(5000), 0)),
                lambda r, c, l: r['required_settings'].update(deep=__import__('functools').reduce(lambda a, _: [a], range(400), 0)),
                lambda r, c, l: (r['required_settings'].update(deep=__import__('functools').reduce(lambda a, _: [a], range(600), 0)),
                                 c[0]['settings'].update(deep=__import__('functools').reduce(lambda a, _: [a], range(600), 0))),
                lambda r, c, l: c[0]['settings'].update(deep=__import__('functools').reduce(lambda a, _: {'k': a}, range(40), 0))]
        for k, mutate in enumerate(wipe):
            with self.subTest(case=k):
                r, c, l = deepcopy(self.r), deepcopy(self.c), deepcopy(self.l); mutate(r, c, l)
                with self.assertRaises(Exception) as caught: select(r, c, l)
                self.assertIs(type(caught.exception), ValueError)
        det = [catalog()[-1]]; det[0]['force_setpoint_n'] = 7.0
        with self.assertRaises(ValueError): select(request_for(catalog()[-1]), det, live_for(det, 'PASS'))
        ok = deepcopy(self.r); ok['metrics'].append(requirement(-0.1, 'value_at_least'))   # A signed lower bound may be negative
        self.c[0]['metrics'] = [dict(G1_METRIC, value=0.05)]
        self.assertEqual(self.run_ok(r=ok)['decision'], 'CONFIRM')

    
    def catalogue_example(self, name):
        import json as _json
        from build_examples import examples
        ex = {e['name']: e for e in examples(catalog())}
        return ex[name]['first']

    def test_C2a_recorded_metrics_are_exactly_the_accepted_values(self):
        by = {p['target']: {(m['quantity'], m['statistic']): m['value'] for m in p['metrics']} for p in catalog()[:4]}
        self.assertEqual(by['A'][('normal_force_error', 'RMSE')], 0.048505)
        self.assertNotIn(('normal_force_error', 'maximum_absolute'), by['A'])   # This metric is absent from the accepted source record for A.
        self.assertAlmostEqual(by['D'][('normal_force_error', 'maximum_absolute')], 0.34485120590670704, places=15)
        self.assertAlmostEqual(by['B'][('normal_force_error', 'maximum_absolute')], 0.22620939123324568, places=15)
        self.assertEqual({len(v) for k, v in by.items() if k != 'A'}, {6}); self.assertEqual(len(by['A']), 5)
        self.assertEqual([p['metrics'] for p in catalog()[4:]], [[], []])                  # No UR3 or UR5 metric is invented

    def test_C2d_every_recorded_metric_literal(self):
        # Check every metric value and all six definition fields independently.
        W = 'declared_4s_scored_window_800_samples'; SC = 'one_recorded_run_not_a_future_guarantee'
        F = {'rmse': ('normal_force_error', 'RMSE', W, 'right_active_pad', 'N'),
             'max': ('normal_force_error', 'maximum_absolute', W, 'right_active_pad', 'N'),
             'mean': ('normal_force', 'mean', W, 'right_active_pad', 'N'),
             'target': ('in_plane_target_error', 'single_selection', 'selection_stage_detector_frame', 'declared_target_marker', 'mm'),
             'service': ('moving_service', 'fraction_of_samples', W, 'task_rule_right_active_idle_left_maintained', 'percent'),
             'path': ('qualified_travel', 'path_length', W, 'right_active_pad', 'mm')}
        want = {
            'G1_A_7N': [('rmse', 0.048505), ('mean', 7.001171), ('target', 0.982534), ('service', 93.75), ('path', 68.2874)],
            'G1_B_7N': [('rmse', 0.048097225595427354), ('max', 0.22620939123324568), ('mean', 7.005952743600581),
                        ('target', 1.0962853346056713), ('service', 99.125), ('path', 71.51866766272262)],
            'G1_C_7N': [('rmse', 0.05180726394773968), ('max', 0.2412338925507811), ('mean', 7.001219009601492),
                        ('target', 1.1084879605507887), ('service', 93.875), ('path', 67.77074921074785)],
            'G1_D_7N': [('rmse', 0.04794851230404908), ('max', 0.34485120590670704), ('mean', 7.004439106798887),
                        ('target', 4.12910298134153), ('service', 95.75), ('path', 70.1532426135274)]}
        got = {p['id']: p['metrics'] for p in catalog()}
        for pid, rows in want.items():
            expected = [dict(zip(('quantity', 'statistic', 'window', 'side', 'unit'), F[k]), scope=SC, value=v) for k, v in rows]
            self.assertEqual(got[pid], expected, pid)
        self.assertEqual(sum(len(r) for r in want.values()), 23)
        self.assertEqual(got['UR3_registered_handover'], []); self.assertEqual(got['UR5_whiteboard_detection'], [])

    def test_C2b_metric_examples(self):
        want = {'frozen_criteria_A_to_D': ('ASK', ['G1_A_7N', 'G1_B_7N', 'G1_C_7N', 'G1_D_7N']),
                'post_hoc_D_rmse_le_0p1N': ('CONFIRM', ['G1_D_7N']), 'post_hoc_D_max_le_0p3N': ('REFUSE', []),
                'post_hoc_B_max_le_0p3N': ('CONFIRM', ['G1_B_7N']), 'post_hoc_A_max_le_0p3N': ('WITHHOLD', ['G1_A_7N']),
                'post_hoc_A_to_D_max_le_0p3N': ('ASK', ['G1_B_7N', 'G1_C_7N']), 'post_hoc_D_whole_job_rmse': ('WITHHOLD', ['G1_D_7N']),
                'post_hoc_D_rmse_live_unobserved': ('WITHHOLD', [])}
        for name, (decision, remaining) in want.items():
            with self.subTest(name=name):
                out = self.catalogue_example(name)
                self.assertEqual((out['decision'], out['remaining']), (decision, remaining))

    def test_C2c_requested_target_is_never_substituted(self):
        out = self.catalogue_example('post_hoc_D_max_le_0p3N')
        self.assertEqual(out['decision'], 'REFUSE'); self.assertIsNone(out['configuration'])
        self.assertEqual(sorted(out['not_applicable']), ['G1_A_7N', 'G1_B_7N', 'G1_C_7N'])   # B is not offered for a D request

    # Snapshot semantics (documented, unchanged)
    def test_S1_new_observation_needs_new_confirmation_even_with_equal_facts(self):
        self.l['snapshot_id'] = 'obs-001'; token = self.token(self.choose())
        l2 = deepcopy(self.l); l2['snapshot_id'] = 'obs-002'
        self.assertEqual(select(self.r, self.c, l2, confirmation=token)['decision'], 'CONFIRM')

    def test_S2_declaration_is_not_one_use(self):
        token = self.token(self.choose())
        self.assertEqual([self.choose(confirmation=token)['decision'] for _ in range(3)], ['SELECT'] * 3)

    # Source mapping
    def test_M1_location_A_keeps_its_own_qualification(self):
        basis = {p['target']: p['assessment_basis'] for p in catalog()[:4]}
        self.assertEqual(len(set(basis.values())), 4)
        for phrase in ('retrospective check', 'latch-diagnostic run', '20 mm from C'):
            self.assertIn(phrase, basis['A'])

    def test_M2_catalog_holds_only_the_recorded_7N_setpoint(self):
        self.assertEqual({p['force_setpoint_n'] for p in catalog() if p['capability'] == 'regulated_wipe'}, {7.0})
        self.assertEqual(json.dumps(catalog(), sort_keys=True), json.dumps(catalog(), sort_keys=True))


if __name__ == '__main__':
    unittest.main()
