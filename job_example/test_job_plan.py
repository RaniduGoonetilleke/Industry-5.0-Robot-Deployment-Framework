"""Test task assignments, human roles, place checks and declaration handling.

Scenarios, observations and approvals in these tests are synthetic inputs."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from job_plan import plan, load_catalogue, load_human_roles, base_live, declaration_from_plan, STATES   # noqa: E402
sys.path.insert(0, str(ROOT / 'selector'))
from selector import select                                                           # noqa: E402

SELECTOR_SHA256 = '922cb93fa1265a8cc50987812d3c062345b99aa2b676536173a68f21eef22d1e'
CAT = load_catalogue()
FROZEN = json.loads((ROOT / 'analysis/CANDIDATE_MAPPING.json').read_text())['profiles']     # Historical six-profile catalogue
EARLIER = [c for c in CAT if not c['id'].endswith('_8N')]      # Compare force settings while keeping the UR5 human-role assessment unchanged.
ROLES = load_human_roles()
BUILDING = json.loads((HERE / 'scenarios/customer_building.json').read_text())
TESTED = json.loads((HERE / 'scenarios/tested_contexts.json').read_text())
DECL = TESTED['synthetic_declarations']
PLATFORM = {c['id']: c['platform'] for c in CAT + FROZEN}
MANUAL_WIPE = {'mode': 'owner_permitted_manual_wipe', 'criteria': 'manual task only; no force guarantee',
               'training': 'whiteboard_wiping', 'produces': [], 'contact': True}


def by_id(result):
    return {r['id']: r for r in result['tasks']}


def states(result):
    return {r['id']: (r['state'], r['robot'] and r['robot']['configuration'], r['person']) for r in result['tasks']}


def task_of(scenario, tid):
    return next(t for t in scenario['tasks'] if t['id'] == tid)


def set_location(scenario, tid, **values):
    for slot in scenario['slots']:
        scenario['conditions'][slot]['locations'][task_of(scenario, tid)['location']].update(values)


def without_ur5_tasks(scenario):
    """Remove the UR5 detection and dependent wipe so H1 is free in slot-1."""
    s = copy.deepcopy(scenario)
    s['tasks'] = [t for t in s['tasks'] if t['id'] not in ('T-WB-L', 'T-WB-W')]
    return s


def isolated_d(minimum, maximum):
    """Use the D task alone so earlier reservations cannot affect its live inputs."""
    s = copy.deepcopy(TESTED)
    s.pop('synthetic_declarations')
    s['tasks'] = [task_of(s, 'T-WS-D')]
    s['tasks'][0]['request']['force'].update(minimum_n=minimum, maximum_n=maximum)
    return s


def new_declaration(scenario, catalogue=CAT, tid='T-WS-D', **intent):
    """Build a new synthetic approval from this scenario variant's own plan."""
    row = by_id(plan(scenario, catalogue))[tid]
    return {tid: declaration_from_plan(row, snapshot_id=scenario['live']['snapshot_id'], basis='test: new synthetic approval',
                                       authored_from='test pre-declaration plan', **intent)}


class Invariants(unittest.TestCase):
    def check_invariants(self, scenario, result, catalogue):
        tasks = {t['id']: t for t in scenario['tasks']}
        people = {p['id']: p for p in scenario['team']['people']}
        team = set(scenario['team']['robots'])
        self.assertEqual(sorted(r['id'] for r in result['tasks']), sorted(tasks))                     
        seen_robot, seen_person = set(), set()
        for r in result['tasks']:
            self.assertIn(r['state'], STATES)
            if r['robot']:                                                                               
                self.assertIn(r['selector']['decision'], ('SELECT', 'CONFIRM'))
                self.assertIn(r['state'], ('RECOMMENDED', 'PENDING_CONFIRMATION'))
                self.assertIn(r['robot']['platform'], team)                                              
                key = (r['slot'], r['robot']['platform']); self.assertNotIn(key, seen_robot); seen_robot.add(key)   
            if r['selector']:                                                                            
                offered = r['selector']['remaining'] + ([r['selector']['configuration']] if r['selector']['configuration'] else [])
                self.assertTrue(all(PLATFORM[c] in team for c in offered), (r['id'], offered))
            if r['person']:                                                                              
                p = people[r['person']]
                self.assertEqual(p['availability'][r['slot']], 'AVAILABLE')
                for role in r['person_role']:
                    if role.startswith('manual:'):
                        self.assertIn(role[len('manual:'):], p['training'])
                    else:
                        self.assertIn(role, p['roles'])
                key = (r['slot'], r['person']); self.assertNotIn(key, seen_person); seen_person.add(key)             
            if r['state'] == 'MANUAL_ASSIGNED' and (tasks[r['id']].get('request') or {}).get('capability') == 'regulated_wipe':
                self.assertFalse(r['manual_alternative']['original_request_fulfilled'])                
            if r['state'] in ('CONDITIONAL_ON_INPUT', 'BLOCKED_INPUT'):                                   
                self.assertIsNone(r['robot']); self.assertIsNone(r['person']); self.assertIsNone(r['selector'])
            if r['state'] == 'MANUAL_ASSIGNED':                                                          
                self.assertEqual(r['route'], 'manual')
            if r['state'] in ('RECOMMENDED', 'PENDING_CONFIRMATION', 'PENDING_CHOICE', 'WAITING'):
                self.assertEqual(r['route'], 'robot')

    def test_generated_variants_satisfy_the_invariants(self):
        for scenario, decl in ((BUILDING, None), (TESTED, None), (TESTED, DECL)):
            self.check_invariants(scenario, plan(scenario, CAT, declarations=decl), CAT)

    def test_customer_building_outcomes(self):
        r = by_id(plan(BUILDING, CAT))
        for t in ('WB1-L', 'WB2-L', 'WB3-L', 'WS1', 'WS2', 'WS3', 'SH1'):
            self.assertEqual((r[t]['state'], r[t]['selector']['decision']), ('BLOCKED_EVIDENCE', 'WITHHOLD'), t)
        for t in ('WB1-W', 'WB2-W', 'WB3-W'):
            self.assertEqual(r[t]['state'], 'BLOCKED_INPUT', t)
        self.assertEqual((r['GP1']['state'], r['GP1']['person'], r['GP2']['state'], r['GP2']['person']),
                         ('MANUAL_ASSIGNED', 'H1', 'MANUAL_ASSIGNED', 'H2'))
        gap = {g['candidate']: set(g['mismatch']) for g in r['WS1']['gap']}
        self.assertTrue({'domain', 'context', 'target'} <= gap['G1_D_8N'])   # Simulation record, new physical context

    def test_tested_context_outcomes_before_and_after_synthetic_declarations(self):
        before = by_id(plan(TESTED, CAT))
        self.assertEqual((before['T-WB-L']['state'], before['T-WB-L']['person'], before['T-WB-L']['person_role']),
                         ('RECOMMENDED', 'H1', ['attendant']))          # The UR5 option reserves its attendant
        self.assertEqual((before['T-WS-D']['state'], before['T-WS-D']['selector']['remaining']), ('PENDING_CHOICE', ['G1_D_7N', 'G1_D_8N']))
        self.assertEqual((before['T-SH']['state'], before['T-SH']['person']), ('PENDING_CONFIRMATION', 'H1'))
        self.assertEqual(before['T-WS-AB']['state'], 'BLOCKED_EVIDENCE')
        self.assertEqual(before['T-WB-W']['state'], 'CONDITIONAL_ON_INPUT')
        after = by_id(plan(TESTED, CAT, declarations=DECL))
        # H1 attends the UR5 detection in slot-1, so the declared confirmation of the G1 wipe cannot be applied there
        self.assertEqual((after['T-WS-D']['state'], after['T-WS-D']['robot'], after['T-WS-D']['person']), ('WAITING_FOR_PERSON', None, None))
        self.assertEqual(after['T-WS-D']['synthetic'], ['choice of G1_D_8N by owner'])
        self.assertIn('declared confirmation not applied: H1 is occupied in slot-1 by T-WB-L', after['T-WS-D']['notes'])
        free = without_ur5_tasks(TESTED)                       # Control: with H1 free, a fresh declaration applies
        fresh = new_declaration(free, choice=('G1_D_8N', 'owner'), confirm=('G1_D_8N', 'H1'))
        control = by_id(plan(free, CAT, declarations=fresh))['T-WS-D']
        self.assertEqual((control['state'], control['robot']['configuration'], control['person'], control['person_role']),
                         ('RECOMMENDED', 'G1_D_8N', 'H1', ['confirmer', 'operator']))
        self.assertIn('confirmation of G1_D_8N by H1', control['synthetic'])
        self.assertEqual(after['T-SH']['state'], 'RECOMMENDED')
        self.assertEqual(after['T-WB-W']['state'], 'CONDITIONAL_ON_INPUT')                               # No completion injected


class NegativeControls(unittest.TestCase):
    def variant(self, scenario, **conditions):
        s = copy.deepcopy(scenario)
        for slot in s['slots']:
            s['conditions'][slot]['shared_safety'] = conditions.get('shared', 'PASS')
            for area in s['conditions'][slot]['areas']:
                s['conditions'][slot]['areas'][area] = conditions.get('area', 'PASS')
        return s

    def test_shared_or_area_fail_or_unknown_never_assigns_a_trained_available_person_or_robot(self):   
        for kind, value, expected in (('shared', 'FAIL', 'BLOCKED_SHARED'), ('shared', 'UNKNOWN', 'WITHHELD_SHARED'),
                                      ('area', 'FAIL', 'BLOCKED_SHARED'), ('area', 'UNKNOWN', 'WITHHELD_SHARED')):
            for scenario in (BUILDING, TESTED):
                result = plan(self.variant(scenario, **{kind: value}), CAT)
                for r in result['tasks']:
                    self.assertIsNone(r['robot'], (kind, value, r['id'])); self.assertIsNone(r['person'], (kind, value, r['id']))
                    if not r['inputs']:
                        self.assertEqual(r['state'], expected, (kind, value, r['id']))
                if kind == 'shared':   # The selector's own shared-safety result is recorded
                    decisions = {r['selector']['decision'] for r in result['tasks'] if r['selector']}
                    self.assertEqual(decisions, {'REFUSE' if value == 'FAIL' else 'WITHHOLD'})

    def test_robot_double_booking_gives_wait(self):                                                    
        s = copy.deepcopy(TESTED)
        second = copy.deepcopy(s['tasks'][0]); second['id'] = second['request']['id'] = 'T-WB-L2'
        s['tasks'].insert(1, second)
        r = by_id(plan(s, CAT))
        self.assertEqual((r['T-WB-L']['state'], r['T-WB-L2']['state']), ('RECOMMENDED', 'WAITING'))
        self.assertEqual(r['T-WB-L2']['selector']['decision'], 'WAIT')

    def test_person_double_booking_gives_waiting_for_person(self):                                     
        s = copy.deepcopy(TESTED)
        task_of(s, 'T-SH')['slot'] = 'slot-1'        # H1 already attends the UR5 detection in slot-1
        r = by_id(plan(s, CAT, declarations=s['synthetic_declarations']))
        self.assertEqual((r['T-WB-L']['person'], r['T-WB-L']['person_role']), ('H1', ['attendant']))
        for tid in ('T-WS-D', 'T-SH'):               # One person holds one occupancy per slot
            self.assertEqual((r[tid]['state'], r[tid]['robot'], r[tid]['person']), ('WAITING_FOR_PERSON', None, None), tid)
        self.assertEqual(list(plan(s, CAT, declarations=s['synthetic_declarations'])['reservations']['people'].values()), ['T-WB-L'])
        b = copy.deepcopy(BUILDING)
        b['team']['people'][1]['availability']['slot-1'] = 'UNAVAILABLE'
        rb = by_id(plan(b, CAT))
        self.assertEqual((rb['GP1']['person'], rb['GP2']['state'], rb['GP2']['person']), ('H1', 'WAITING_FOR_PERSON', None))

    def test_no_person_without_declared_training_or_manual_alternative(self):                         
        b = copy.deepcopy(BUILDING)
        for p in b['team']['people']:
            p['training'] = []
        r = by_id(plan(b, CAT))
        self.assertEqual({r['GP1']['state'], r['GP2']['state']}, {'WAITING_FOR_PERSON'})
        for t in ('WS1', 'SH1'):                                  # No manual alternative declared: stays blocked
            self.assertEqual(by_id(plan(BUILDING, CAT))[t]['state'], 'BLOCKED_EVIDENCE')

    def test_dependency_needs_its_input_and_a_synthetic_completion_is_labelled(self):                  
        r = by_id(plan(TESTED, CAT, completions=[('T-WB-L', 'located_region')]))
        w = r['T-WB-W']
        self.assertIn('completion of T-WB-L', w['synthetic'])
        self.assertEqual((w['state'], w['selector']['decision']), ('BLOCKED_EVIDENCE', 'WITHHOLD'))   # No whiteboard wipe record

    def test_confirm_stays_pending_select_with_bound_declaration_and_pending_again_after_snapshot_change(self):  
        self.assertEqual(by_id(plan(TESTED, CAT))['T-SH']['state'], 'PENDING_CONFIRMATION')
        self.assertEqual(by_id(plan(TESTED, CAT, declarations={'T-SH': DECL['T-SH']}))['T-SH']['state'], 'RECOMMENDED')
        changed = copy.deepcopy(TESTED); changed['live']['snapshot_id'] += '_CHANGED'   # The same declarations, a new snapshot
        stale = by_id(plan(changed, CAT, declarations=DECL))
        self.assertEqual((stale['T-SH']['state'], stale['T-WS-D']['state']), ('PENDING_CONFIRMATION', 'PENDING_CHOICE'))
        unnamed = copy.deepcopy(DECL['T-SH']); unnamed['confirm'].pop('by')                 # A confirmation must name its person
        self.assertEqual(by_id(plan(TESTED, CAT, declarations={'T-SH': unnamed}))['T-SH']['state'], 'PENDING_CONFIRMATION')
        other = copy.deepcopy(DECL['T-SH']); other['confirm']['by'] = 'H2'                   # H2 holds no confirmer role
        self.assertEqual(by_id(plan(TESTED, CAT, declarations={'T-SH': other}))['T-SH']['state'], 'PENDING_CONFIRMATION')
        task = task_of(TESTED, 'T-SH')
        live = base_live(CAT, TESTED)
        first = select(task['request'], CAT, live)
        conf = {'kind': 'confirmation', 'binding': first['binding'], 'candidate_id': first['configuration']['id']}
        self.assertEqual(select(task['request'], CAT, live, confirmation=conf)['decision'], 'SELECT')
        changed = copy.deepcopy(live); changed['snapshot_id'] += '_changed'
        self.assertEqual(select(task['request'], CAT, changed, confirmation=conf)['decision'], 'CONFIRM')

    def test_ask_stays_open_without_a_declared_choice(self):                                           
        only_confirm = {k: v for k, v in DECL['T-WS-D'].items() if k != 'choice'}
        r = by_id(plan(TESTED, CAT, declarations={'T-WS-D': only_confirm}))
        self.assertEqual((r['T-WS-D']['state'], r['T-WS-D']['robot']), ('PENDING_CHOICE', None))

    def test_a_declaration_never_confirms_an_option_it_does_not_name(self):                          
        r = by_id(plan(TESTED, FROZEN, declarations=DECL))   # The 7 N catalogue has no G1_D_8N option.
        self.assertEqual((r['T-WS-D']['state'], r['T-WS-D']['robot']['configuration']), ('PENDING_CONFIRMATION', 'G1_D_7N'))
        self.assertFalse(any(s.startswith('confirmation') for s in r['T-WS-D']['synthetic']))

    def test_local_hazard_or_contact_permission_blocks_people_too(self):                             
        for field, value, expected in (('safety', 'FAIL', 'BLOCKED_TASK_CONDITION'), ('safety', 'UNKNOWN', 'WITHHELD_TASK_CONDITION'),
                                       ('contact_permission', 'FAIL', 'BLOCKED_TASK_CONDITION'),
                                       ('contact_permission', 'UNKNOWN', 'WITHHELD_TASK_CONDITION')):
            s = copy.deepcopy(TESTED)
            set_location(s, 'T-WS-D', **{field: value})
            task_of(s, 'T-WS-D')['manual_alternative'] = {'mode': 'manual_wipe_unregulated', 'training': 'glass_wiping', 'criteria': 'test', 'contact': True}
            r = by_id(plan(s, CAT))['T-WS-D']
            self.assertEqual((r['state'], r['person'], r['robot']), (expected, None, None), (field, value))

    def test_robot_specific_withholding_is_labelled_and_may_use_a_declared_manual_alternative(self):  
        s = copy.deepcopy(TESTED); s['live']['platform_availability']['G1'] = 'UNKNOWN'
        r = by_id(plan(s, CAT))['T-WS-D']
        self.assertEqual((r['state'], r['reasons']), ('WITHHELD_ROBOT_CHECK', ['platform availability unknown']))
        task_of(s, 'T-WS-D')['manual_alternative'] = {'mode': 'manual_wipe_unregulated', 'training': 'glass_wiping', 'criteria': 'test', 'contact': True}
        r = by_id(plan(s, CAT))['T-WS-D']
        self.assertEqual((r['state'], r['manual_alternative']['original_request_fulfilled']), ('MANUAL_ASSIGNED', False))

    def test_declared_outputs_are_checked(self):                                                    
        s = copy.deepcopy(BUILDING)
        s['tasks'][-1]['needs'] = [['WB3-L', 'undeclared_output']]
        with self.assertRaises(ValueError):
            plan(s, CAT)
        s = copy.deepcopy(BUILDING)
        wb1 = task_of(s, 'WB1-L')
        wb1['manual_alternative'] = {'mode': 'manual_inspection', 'training': 'whiteboard_wiping', 'criteria': 'test',
                                     'contact': False}   # Produces nothing
        self.assertEqual(by_id(plan(s, CAT))['WB1-W']['state'], 'BLOCKED_INPUT')
        wb1['manual_alternative']['produces'] = ['located_region']
        self.assertEqual(by_id(plan(s, CAT))['WB1-W']['state'], 'CONDITIONAL_ON_INPUT')


class Team(unittest.TestCase):                                                                        
    def with_team(self, scenario, robots):
        s = copy.deepcopy(scenario); s['team']['robots'] = robots
        return s

    def test_complete_partial_and_empty_teams(self):
        for robots in (['UR3', 'UR5', 'G1'], ['UR3', 'G1'], ['UR5'], ['G1'], []):
            for scenario in (BUILDING, TESTED):
                for decl in ((None, DECL) if scenario is TESTED else (None,)):
                    s = self.with_team(scenario, robots)
                    result = plan(s, CAT, declarations=decl)
                    Invariants.check_invariants(self, s, result, CAT)
                    self.assertEqual(result['scope_queries'] == 0, len(robots) == 3)   # Only a partial team needs one
        empty = by_id(plan(self.with_team(TESTED, []), CAT, declarations=DECL))
        self.assertEqual({k: v['state'] for k, v in empty.items()},
                         {'T-WB-L': 'BLOCKED_NOT_IN_TEAM', 'T-WS-D': 'BLOCKED_NOT_IN_TEAM', 'T-WS-AB': 'BLOCKED_EVIDENCE',
                          'T-SH': 'BLOCKED_NOT_IN_TEAM', 'T-WB-W': 'BLOCKED_INPUT'})
        self.assertEqual([e['candidate'] for e in empty['T-WS-D']['outside_team']], ['G1_D_7N', 'G1_D_8N'])
        self.assertFalse(any(v['robot'] or v['synthetic'] for v in empty.values()))
        partial = by_id(plan(self.with_team(TESTED, ['UR3', 'G1']), CAT, declarations=DECL))
        self.assertEqual((partial['T-WB-L']['state'], partial['T-WB-L']['outside_team']),
                         ('BLOCKED_NOT_IN_TEAM', [{'candidate': 'UR5_whiteboard_detection', 'platform': 'UR5'}]))
        self.assertEqual(partial['T-WB-W']['state'], 'BLOCKED_INPUT')
        # A changed team is a changed context: the delivered approvals are not carried over
        self.assertEqual((partial['T-WS-D']['state'], partial['T-SH']['state']), ('PENDING_CHOICE', 'PENDING_CONFIRMATION'))
        self.assertEqual(states(plan(self.with_team(TESTED, ['G1', 'UR5', 'UR3']), CAT, declarations=DECL)),
                         states(plan(TESTED, CAT, declarations=DECL)))                      # Order of the roster is immaterial

    def test_an_absent_robot_may_be_replaced_only_by_a_declared_manual_alternative(self):
        s = self.with_team(TESTED, ['UR3', 'G1'])
        task_of(s, 'T-WB-L')['manual_alternative'] = dict(MANUAL_WIPE, mode='manual_marking', produces=['located_region'])
        r = by_id(plan(s, CAT))
        self.assertEqual((r['T-WB-L']['state'], r['T-WB-L']['person'], r['T-WB-L']['route']), ('MANUAL_ASSIGNED', 'H1', 'manual'))
        self.assertEqual(r['T-WB-W']['state'], 'CONDITIONAL_ON_INPUT')
        set_location(s, 'T-WB-L', safety='FAIL')                   # The manual route still follows the location's conditions
        r = by_id(plan(s, CAT))['T-WB-L']
        self.assertEqual((r['state'], r['person']), ('BLOCKED_TASK_CONDITION', None))

    def test_malformed_or_unknown_roster_entries_are_rejected(self):
        for robots in ('UR3', ['UR3', 'UR3'], ['UR3', None], ['UR3', ' '], ['UR10'], {'UR3': True}):
            with self.assertRaises(ValueError, msg=repr(robots)):
                plan(self.with_team(TESTED, robots), CAT)
        s = copy.deepcopy(TESTED); s['live']['platform_availability']['UR10'] = 'AVAILABLE'
        with self.assertRaises(ValueError):
            plan(s, CAT)
        s = copy.deepcopy(TESTED); s['live']['candidate_overrides'] = {'UR10_arm': {'checks': {'path': 'FAIL'}}}
        with self.assertRaises(ValueError):
            plan(s, CAT)
        unused = plan(self.with_team(TESTED, ['G1']), CAT)['unused_live_facts']
        self.assertEqual(unused, ['platform_availability.UR3 (not in the team)', 'platform_availability.UR5 (not in the team)'])


class DeclarationBinding(unittest.TestCase):                                                          
    def test_delivered_declarations_carry_the_pre_declaration_bindings(self):
        pre = by_id(plan(TESTED, CAT))
        for tid, d in DECL.items():
            self.assertEqual(d['job_context'], pre[tid]['job_context'])
            for kind in ('choice', 'confirm'):
                if kind in d:
                    self.assertEqual(d[kind]['binding'], pre[tid]['selector']['binding'])
        before = json.dumps(TESTED, sort_keys=True)
        plan(TESTED, CAT, declarations=DECL)
        self.assertEqual(json.dumps(TESTED, sort_keys=True), before)                  # Plan() never writes a declaration

    def stale_variants(self, base):
        out = {}
        s = copy.deepcopy(base); s['tasks'][0]['request']['force']['minimum_n'] -= 0.1; out['request'] = (s, CAT)
        s = copy.deepcopy(base); s['live']['platform_availability']['UR5'] = 'BUSY'; out['live fact'] = (s, CAT)
        c = copy.deepcopy(CAT); next(p for p in c if p['id'] == 'G1_D_8N')['assessment_basis'] += ' [Review updated.]'
        out['assessment'] = (base, c)
        s = copy.deepcopy(base); s['live']['snapshot_id'] += '_CHANGED'; out['snapshot label'] = (s, CAT)
        s = copy.deepcopy(base); s['team']['robots'] = ['UR5', 'G1']; out['team'] = (s, CAT)
        s = copy.deepcopy(base); s['tasks'][0]['slot'] = 'slot-2'; out['job context: slot'] = (s, CAT)
        s = copy.deepcopy(base); s['tasks'][0]['robot_roles'] = ['operator', 'attendant']; out['job context: roles'] = (s, CAT)
        s = copy.deepcopy(base)                            # The target re-associated with another declared place
        for slot in s['slots']:
            s['conditions'][slot]['locations']['g1_wall_location_D_panel'] = {'safety': 'PASS', 'contact_permission': 'PASS'}
        s['places']['g1_wall_location_D_panel'] = {'area': 'g1_simulated_wall', 'targets': s['places']['g1_wall_location_D']['targets']}
        s['places']['g1_wall_location_D'] = {'area': 'g1_simulated_wall', 'targets': []}
        s['tasks'][0]['location'] = 'g1_wall_location_D_panel'; out['job context: location'] = (s, CAT)
        s = copy.deepcopy(base)                            # The place record itself changed
        s['places']['g1_wall_location_D']['targets'].append(
            {'domain': 'simulation', 'context': 'supported_original_wall_point_pilot', 'target': 'D_panel'})
        out['job context: place record'] = (s, CAT)
        return out

    def check(self, base, intent, pending, applied):
        decl = new_declaration(base, **intent)
        task = base['tasks'][0]
        control = by_id(plan(base, CAT, declarations=decl))['T-WS-D']
        self.assertEqual(control['state'], applied)                                            # Unchanged declaration applies
        self.assertTrue(control['synthetic'])
        original = decl['T-WS-D']['confirm' if 'confirm' in intent else 'choice']['binding']
        for name, (scenario, catalogue) in self.stale_variants(base).items():
            row = by_id(plan(scenario, catalogue, declarations=decl))['T-WS-D']
            self.assertEqual((row['state'], row['synthetic']), (pending, []), name)
            if not name.startswith('job context'):                                              # The selector agrees
                t = scenario['tasks'][0]
                live = base_live(catalogue, scenario)
                team = [c for c in catalogue if c['platform'] in scenario['team']['robots']]
                live['candidates'] = {c['id']: live['candidates'][c['id']] for c in team}
                kind = 'confirmation' if 'confirm' in intent else 'choice'
                direct = select(t['request'], team, live, **{kind: {'kind': kind, 'binding': original,
                                                                     'candidate_id': 'G1_D_8N'}})
                self.assertEqual(direct['decision'], 'CONFIRM' if kind == 'confirmation' else 'ASK', name)
                self.assertNotEqual(direct['binding'], original, name)
            else:                                                                               # The layer's own constraints
                self.assertIn('job context changed', ' '.join(row['notes']), name)
        return decl

    def test_stale_contents_or_context_leave_a_confirmation_pending(self):
        base = isolated_d(8.0, 8.0)                                            # Exact 8 N: CONFIRM G1_D_8N directly
        self.check(base, {'confirm': ('G1_D_8N', 'H1')}, 'PENDING_CONFIRMATION', 'RECOMMENDED')

    def test_stale_contents_or_context_leave_a_choice_pending(self):
        base = isolated_d(6.0, 8.0)                                            # 6-8 N: ASK between D/7 N and D/8 N
        self.check(base, {'choice': ('G1_D_8N', 'owner')}, 'PENDING_CHOICE', 'PENDING_CONFIRMATION')

    def test_a_new_approval_is_a_separate_labelled_input(self):
        base = isolated_d(8.0, 8.0)
        old = new_declaration(base, confirm=('G1_D_8N', 'H1'))
        changed = copy.deepcopy(base); changed['tasks'][0]['request']['force']['minimum_n'] = 7.9
        self.assertEqual(by_id(plan(changed, CAT, declarations=old))['T-WS-D']['state'], 'PENDING_CONFIRMATION')
        fresh = new_declaration(changed, confirm=('G1_D_8N', 'H1'))
        self.assertNotEqual(fresh['T-WS-D']['confirm']['binding'], old['T-WS-D']['confirm']['binding'])
        row = by_id(plan(changed, CAT, declarations=fresh))['T-WS-D']
        self.assertEqual((row['state'], row['synthetic']), ('RECOMMENDED', ['confirmation of G1_D_8N by H1']))

    def test_wrong_candidates_people_and_malformed_declarations(self):
        base = isolated_d(8.0, 8.0)
        wrong = new_declaration(base, confirm=('G1_D_7N', 'H1'))
        self.assertEqual(by_id(plan(base, CAT, declarations=wrong))['T-WS-D']['state'], 'PENDING_CONFIRMATION')
        nobody = new_declaration(base, confirm=('G1_D_8N', 'H2'))                  # H2 is no confirmer
        self.assertEqual(by_id(plan(base, CAT, declarations=nobody))['T-WS-D']['state'], 'PENDING_CONFIRMATION')
        busy = copy.deepcopy(base); busy['team']['people'][0]['availability']['slot-1'] = 'BUSY'
        self.assertEqual(by_id(plan(busy, CAT, declarations=new_declaration(base, confirm=('G1_D_8N', 'H1'))))['T-WS-D']['state'],
                         'WAITING_FOR_PERSON')
        ask = isolated_d(6.0, 8.0)
        other = new_declaration(ask, choice=('G1_A_8N', 'owner'))                  # Not among the remaining options
        row = by_id(plan(ask, CAT, declarations=other))['T-WS-D']
        self.assertEqual(row['state'], 'PENDING_CHOICE')
        self.assertIn('not among the remaining options', ' '.join(row['notes']))
        good = new_declaration(base, confirm=('G1_D_8N', 'H1'))['T-WS-D']
        r26_form = {'T-WS-D': {'snapshot_id': base['live']['snapshot_id'], 'confirm': {'candidate_id': 'G1_D_8N', 'by': 'H1'}}}
        for bad in (r26_form, {'T-WS-D': dict(good, confirm={'candidate_id': 'G1_D_8N', 'by': 'H1'})},
                    {'T-WS-D': dict(good, confirm=dict(good['confirm'], binding='0' * 63))},
                    {'T-WS-D': {k: v for k, v in good.items() if k != 'job_context'}}, {'T-NOPE': good}):
            with self.assertRaises(ValueError):
                plan(base, CAT, declarations=bad)


class LocationConditions(unittest.TestCase):                                                          
    def d_task(self, minimum, maximum, alt=True, **location):
        s = without_ur5_tasks(TESTED)
        t = task_of(s, 'T-WS-D')
        t['request']['force'].update(minimum_n=minimum, maximum_n=maximum)
        if alt:
            t['manual_alternative'] = dict(MANUAL_WIPE)
        set_location(s, 'T-WS-D', **location)
        return s

    def test_location_fail_or_unresolved_blocks_every_route(self):
        for field in ('safety', 'contact_permission'):
            for value, expected in (('FAIL', 'BLOCKED_TASK_CONDITION'), ('UNKNOWN', 'WITHHELD_TASK_CONDITION'),
                                    ('CONFLICTING', 'WITHHELD_TASK_CONDITION')):
                for force, in_scope in (((9.0, 9.0), False), ((6.0, 8.0), True), ((8.0, 8.0), True), ((7.5, 7.5), False)):
                    s = self.d_task(*force, **{field: value})
                    result = plan(s, CAT)
                    r = by_id(result)['T-WS-D']
                    self.assertEqual((r['state'], r['robot'], r['person'], r['route']), (expected, None, None, None),
                                     (field, value, force))
                    self.assertFalse(any(v == 'T-WS-D' for v in result['reservations']['people'].values()))
                    decision = ('REFUSE' if value == 'FAIL' else 'WITHHOLD') if in_scope else 'WITHHOLD'
                    self.assertEqual(r['selector']['decision'], decision, (field, value, force))   # The selector saw the location

    def test_safe_positive_controls_and_robot_specific_failures_at_the_same_location(self):
        r = by_id(plan(self.d_task(9.0, 9.0), CAT))['T-WS-D']                 # Unsupported setpoint: a robot-specific gap
        self.assertEqual((r['state'], r['person'], r['route']), ('MANUAL_ASSIGNED', 'H1', 'manual'))
        self.assertEqual(by_id(plan(self.d_task(6.0, 8.0), CAT))['T-WS-D']['state'], 'PENDING_CHOICE')
        s = self.d_task(6.0, 8.0); s['live']['candidate_overrides'] = {'G1_D_7N': {'checks': {'path': 'FAIL'}}}
        r = by_id(plan(s, CAT))['T-WS-D']                                         # A path failure belongs to one profile
        self.assertEqual((r['state'], r['robot']['configuration']), ('PENDING_CONFIRMATION', 'G1_D_8N'))
        s = self.d_task(8.0, 8.0); s['live']['platform_safety'] = {'G1': 'FAIL'}
        r = by_id(plan(s, CAT))['T-WS-D']
        self.assertEqual((r['state'], r['reasons']), ('MANUAL_ASSIGNED', ['live:platform_safety']))
        s = self.d_task(8.0, 8.0, alt=False); s['live']['platform_safety'] = {'G1': 'FAIL'}
        self.assertEqual(by_id(plan(s, CAT))['T-WS-D']['state'], 'BLOCKED_ROBOT_CHECK')

    def test_an_unrelated_location_is_unaffected(self):
        nominal = states(plan(TESTED, CAT, declarations=DECL))
        for value in ('FAIL', 'UNKNOWN'):
            s = copy.deepcopy(TESTED); set_location(s, 'T-WS-D', safety=value)
            got = states(plan(s, CAT, declarations=DECL))
            self.assertEqual({k: v for k, v in got.items() if k != 'T-WS-D'}, {k: v for k, v in nominal.items() if k != 'T-WS-D'})
        s = copy.deepcopy(TESTED); set_location(s, 'T-WB-L', contact_permission='FAIL')   # Detection involves no contact...
        r = by_id(plan(s, CAT))
        self.assertEqual(r['T-WB-L']['state'], 'RECOMMENDED')
        self.assertEqual(r['T-WB-W']['state'], 'BLOCKED_TASK_CONDITION')         # ...the wipe does, even before its input exists
        done = by_id(plan(s, CAT, completions=[('T-WB-L', 'located_region')]))['T-WB-W']
        self.assertEqual(done['state'], 'BLOCKED_TASK_CONDITION')

    def test_manual_only_task_follows_the_location(self):
        def manual_only(contact, **location):
            s = without_ur5_tasks(TESTED)
            s['tasks'].append({'id': 'T-MAN', 'label': 'Manual wipe of the tested whiteboard frame', 'slot': 'slot-1',
                               'area': 'ur5_whiteboard_cell', 'location': 'ur5_tested_whiteboard', 'contact': contact,
                               'manual_alternative': dict(MANUAL_WIPE, mode='manual_frame_wipe', contact=contact)})
            for slot in s['slots']:
                s['conditions'][slot]['locations']['ur5_tested_whiteboard'].update(location)
            return by_id(plan(s, CAT))['T-MAN']
        self.assertEqual((manual_only(True)['state'], manual_only(True)['person']), ('MANUAL_ASSIGNED', 'H1'))
        for contact, location, expected in ((True, {'safety': 'FAIL'}, 'BLOCKED_TASK_CONDITION'),
                                            (False, {'safety': 'FAIL'}, 'BLOCKED_TASK_CONDITION'),
                                            (True, {'safety': 'UNKNOWN'}, 'WITHHELD_TASK_CONDITION'),
                                            (True, {'contact_permission': 'FAIL'}, 'BLOCKED_TASK_CONDITION'),
                                            (True, {'contact_permission': 'CONFLICTING'}, 'WITHHELD_TASK_CONDITION'),
                                            (False, {'contact_permission': 'FAIL'}, 'MANUAL_ASSIGNED')):
            r = manual_only(contact, **location)
            self.assertEqual(r['state'], expected, (contact, location))
            self.assertEqual(r['person'] is not None, expected == 'MANUAL_ASSIGNED')

    def test_location_facts_are_never_declared_per_robot_profile(self):
        for override in ({'safety': 'FAIL'}, {'checks': {'contact_authorized': 'FAIL'}}, {'safety': 'PASS'}):
            s = copy.deepcopy(TESTED); s['live']['candidate_overrides'] = {'G1_D_8N': override}
            with self.assertRaises(ValueError):
                plan(s, CAT)
        s = copy.deepcopy(TESTED); task_of(s, 'T-WS-D').pop('location')
        with self.assertRaises(ValueError):
            plan(s, CAT)
        s = copy.deepcopy(TESTED); del s['conditions']['slot-1']['locations']['g1_wall_location_D']
        with self.assertRaises(ValueError):
            plan(s, CAT)
        s = copy.deepcopy(TESTED); set_location(s, 'T-WS-D', safety='MAYBE')
        with self.assertRaises(ValueError):
            plan(s, CAT)
        s = copy.deepcopy(TESTED)
        s['tasks'].append({'id': 'T-MAN', 'label': 'x', 'slot': 'slot-2', 'area': 'ur3_cell', 'location': 'ur3_handover_point',
                           'manual_alternative': dict(MANUAL_WIPE)})                      # No contact declared
        with self.assertRaises(ValueError):
            plan(s, CAT)


class PendingRoutes(unittest.TestCase):                                                               
    def test_a_robot_waiting_for_its_operator_can_still_supply_its_output(self):
        s = copy.deepcopy(TESTED)                  # The UR5 detection needs its attendant; only H1 holds that role
        s['team']['people'][0]['availability']['slot-1'] = 'BUSY'
        r = by_id(plan(s, CAT))
        self.assertEqual((r['T-WB-L']['state'], r['T-WB-L']['route'], r['T-WB-L']['robot']), ('WAITING_FOR_PERSON', 'robot', None))
        self.assertEqual((r['T-WB-W']['state'], r['T-WB-W']['inputs'][0]['status']), ('CONDITIONAL_ON_INPUT', 'awaiting'))
        only_manual = copy.deepcopy(s)                          # The robot route does not borrow the alternative's output
        task_of(only_manual, 'T-WB-L')['produces'] = []
        task_of(only_manual, 'T-WB-L')['manual_alternative'] = dict(MANUAL_WIPE, mode='manual_marking', produces=['located_region'])
        self.assertEqual(by_id(plan(only_manual, CAT))['T-WB-W']['state'], 'BLOCKED_INPUT')

    def test_a_waiting_manual_route_supplies_only_its_declared_outputs(self):
        s = copy.deepcopy(TESTED); s['team']['robots'] = ['UR3', 'G1']
        s['team']['people'][0]['availability']['slot-1'] = 'BUSY'           # No free person with whiteboard training
        task_of(s, 'T-WB-L')['manual_alternative'] = dict(MANUAL_WIPE, mode='manual_marking')   # Declares no output
        r = by_id(plan(s, CAT))
        self.assertEqual((r['T-WB-L']['state'], r['T-WB-L']['route']), ('WAITING_FOR_PERSON', 'manual'))
        self.assertEqual(r['T-WB-W']['state'], 'BLOCKED_INPUT')
        task_of(s, 'T-WB-L')['manual_alternative']['produces'] = ['located_region']
        self.assertEqual(by_id(plan(s, CAT))['T-WB-W']['state'], 'CONDITIONAL_ON_INPUT')


class PreSealReviewFixes(unittest.TestCase):                                                         
    def test_a_robot_sub_task_names_one_target(self):                                                 
        s = copy.deepcopy(TESTED); s.pop('synthetic_declarations')
        task_of(s, 'T-WS-D')['request']['allowed_targets'] = ['A', 'B', 'C', 'D']
        with self.assertRaisesRegex(ValueError, 'several targets'):
            plan(s, CAT)

    def test_a_preference_for_a_robot_outside_the_team_is_refused_explicitly(self):                  
        s = without_ur5_tasks(TESTED); s.pop('synthetic_declarations')
        request = task_of(s, 'T-WS-D')['request']
        request['candidate_priority'], request['candidate_priority_basis'] = ['G1_D_8N', 'G1_D_7N'], 'test: owner prefers 8 N'
        r = by_id(plan(s, CAT))['T-WS-D']
        self.assertEqual((r['state'], r['robot']['configuration']), ('PENDING_CONFIRMATION', 'G1_D_8N'))   # Full team: order applies
        s['team']['robots'] = ['UR3', 'UR5']
        with self.assertRaisesRegex(ValueError, 'outside the declared team'):
            plan(s, CAT)

    def test_contact_is_declared_on_every_manual_alternative(self):                                   
        s = copy.deepcopy(TESTED); s['team']['robots'] = ['UR3', 'G1']                 # The detection needs its manual route
        set_location(s, 'T-WB-L', contact_permission='FAIL')
        for contact, expected in ((True, 'BLOCKED_TASK_CONDITION'), (False, 'MANUAL_ASSIGNED')):
            task_of(s, 'T-WB-L')['manual_alternative'] = dict(MANUAL_WIPE, mode='manual_marking', produces=['located_region'],
                                                               contact=contact)
            self.assertEqual(by_id(plan(s, CAT))['T-WB-L']['state'], expected, contact)
        missing = dict(MANUAL_WIPE, mode='manual_marking'); missing.pop('contact')
        for alt in (missing, dict(MANUAL_WIPE, mode='manual_marking', contact='false')):
            task_of(s, 'T-WB-L')['manual_alternative'] = alt
            with self.assertRaises(ValueError):
                plan(s, CAT)
        r = copy.deepcopy(TESTED); set_location(r, 'T-WB-L', contact_permission='FAIL')
        task_of(r, 'T-WB-L')['contact'] = True                                      # A robot sub-task may declare contact too
        self.assertEqual(by_id(plan(r, CAT))['T-WB-L']['state'], 'BLOCKED_TASK_CONDITION')

    def test_a_known_failure_is_reported_before_inputs_exist(self):                                   
        s = copy.deepcopy(TESTED)
        s['conditions']['slot-1']['locations']['ur5_tested_whiteboard']['safety'] = 'FAIL'          # The locate task's slot only
        r = by_id(plan(s, CAT))
        self.assertEqual((r['T-WB-L']['state'], r['T-WB-W']['state'], r['T-WB-W']['inputs'][0]['status']),
                         ('BLOCKED_TASK_CONDITION', 'BLOCKED_INPUT', 'unobtainable'))
        s = copy.deepcopy(TESTED); s['conditions']['slot-2']['locations']['ur5_tested_whiteboard']['safety'] = 'FAIL'
        r = by_id(plan(s, CAT))
        self.assertEqual((r['T-WB-L']['state'], r['T-WB-W']['state']), ('RECOMMENDED', 'BLOCKED_TASK_CONDITION'))
        s['conditions']['slot-2']['shared_safety'] = 'FAIL'
        self.assertEqual(by_id(plan(s, CAT))['T-WB-W']['state'], 'BLOCKED_SHARED')
        s = copy.deepcopy(TESTED); s['conditions']['slot-2']['locations']['ur5_tested_whiteboard']['safety'] = 'UNKNOWN'
        self.assertEqual(by_id(plan(s, CAT))['T-WB-W']['state'], 'CONDITIONAL_ON_INPUT')     # An unresolved state waits

    def test_confirmation_notes_name_the_actual_reason(self):                                         
        base = isolated_d(8.0, 8.0)
        busy = copy.deepcopy(base); busy['team']['people'][0]['availability']['slot-1'] = 'BUSY'
        row = by_id(plan(busy, CAT, declarations=new_declaration(base, confirm=('G1_D_8N', 'H1'))))['T-WS-D']
        self.assertIn('declared confirmation not applied: H1 is not available in slot-1', row['notes'])
        norole = copy.deepcopy(base); norole['team']['people'][0]['roles'] = ['operator']
        row = by_id(plan(norole, CAT, declarations=new_declaration(base, confirm=('G1_D_8N', 'H1'))))['T-WS-D']
        self.assertIn('declared confirmation not applied: H1 does not hold the role(s) confirmer', row['notes'])
        row = by_id(plan(base, CAT, declarations=new_declaration(base, confirm=('G1_D_8N', 'H9'))))['T-WS-D']
        self.assertIn('declared confirmation not applied: H9 is not a declared person of the team', row['notes'])
        row = by_id(plan(base, CAT, declarations=new_declaration(base, confirm=('G1_D_7N', 'H1'))))['T-WS-D']
        self.assertIn('it names G1_D_7N; the rule proposes G1_D_8N', ' '.join(row['notes']))

    def test_schema_gaps_found_in_review_are_closed(self):                                            # F9, F11
        s = copy.deepcopy(TESTED); s['tasks'].append(copy.deepcopy(s['tasks'][0]))
        with self.assertRaisesRegex(ValueError, 'distinct'):
            plan(s, CAT)
        manual = {'id': 'T-MAN', 'label': 'Manual wipe of the whiteboard frame', 'slot': 'slot-1', 'area': 'ur5_whiteboard_cell',
                  'location': 'whiteboard_frame', 'contact': False, 'manual_alternative': dict(MANUAL_WIPE, contact=False)}
        s = without_ur5_tasks(TESTED); s['tasks'].append(copy.deepcopy(manual))
        s['places']['whiteboard_frame'] = {'area': 'ur5_whiteboard_cell', 'targets': []}     # A place for manual work only
        for slot in s['slots']:
            s['conditions'][slot]['locations']['whiteboard_frame'] = {'safety': 'MAYBE', 'contact_permission': 'PASS'}
        with self.assertRaisesRegex(ValueError, 'safety and contact_permission'):          # A manual-only task: no rule to catch it
            plan(s, CAT)
        s['conditions']['slot-1']['locations']['whiteboard_frame']['safety'] = 'PASS'
        self.assertEqual(by_id(plan(s, CAT))['T-MAN']['state'], 'MANUAL_ASSIGNED')         
        task_of(s, 'T-MAN')['produces'] = ['frame_wiped']
        with self.assertRaisesRegex(ValueError, 'manual-only'):
            plan(s, CAT)
        for completion in (('T-NOPE', 'located_region'), ('T-WB-L', 'other_output')):
            with self.assertRaisesRegex(ValueError, 'no declared output'):
                plan(TESTED, CAT, completions=[completion])
        no_owner = copy.deepcopy(DECL['T-WS-D']); no_owner['choice'].pop('by')
        with self.assertRaisesRegex(ValueError, 'who made it'):
            plan(TESTED, CAT, declarations={'T-WS-D': no_owner})


class PlaceAssociation(unittest.TestCase):                                                           
    G1 = {'domain': 'simulation', 'context': 'supported_original_wall_point_pilot'}

    def target_a(self, location, **condition):
        """Request exactly 7 N at A using the conditions declared for A's own place."""
        s = isolated_d(7.0, 7.0)
        t = s['tasks'][0]; t['request']['allowed_targets'] = ['A']; t['location'] = location
        s['places']['g1_wall_location_A'] = {'area': 'g1_simulated_wall', 'targets': [dict(self.G1, target='A')]}
        for slot in s['slots']:
            s['conditions'][slot]['locations']['g1_wall_location_A'] = dict({'safety': 'PASS', 'contact_permission': 'PASS'}, **condition)
        return s

    def test_a_request_and_its_conditions_must_describe_the_same_place(self):
        for field in ('safety', 'contact_permission'):
            for value, expected in (('FAIL', 'BLOCKED_TASK_CONDITION'), ('UNKNOWN', 'WITHHELD_TASK_CONDITION'),
                                    ('CONFLICTING', 'WITHHELD_TASK_CONDITION')):
                s = self.target_a('g1_wall_location_A', **{field: value})           # Control: the right place's conditions
                r = by_id(plan(s, CAT))['T-WS-D']
                self.assertEqual((r['state'], r['robot'], r['person']), (expected, None, None), (field, value))
                s['tasks'][0]['location'] = 'g1_wall_location_D'                        # Request A while supplying D's conditions.
                with self.assertRaisesRegex(ValueError, 'names place g1_wall_location_A but its location is g1_wall_location_D'):
                    plan(s, CAT)
                with self.assertRaisesRegex(ValueError, 'names place'):                 # A declaration cannot make it acceptable
                    plan(s, CAT, declarations={'T-WS-D': DECL['T-WS-D']})
                s['tasks'][0]['manual_alternative'] = dict(MANUAL_WIPE)                   # Nor a manual route with a free, trained H1
                with self.assertRaisesRegex(ValueError, 'names place'):
                    plan(s, CAT)
        s = self.target_a('g1_wall_location_A')                                          # Control: PASS, fresh approval applies
        pre = by_id(plan(s, CAT))['T-WS-D']
        self.assertEqual((pre['state'], pre['robot']['configuration']), ('PENDING_CONFIRMATION', 'G1_A_7N'))
        after = by_id(plan(s, CAT, declarations=new_declaration(s, confirm=('G1_A_7N', 'H1'))))['T-WS-D']
        self.assertEqual(after['state'], 'RECOMMENDED')

    def test_missing_ambiguous_or_undeclared_associations_fail_closed(self):
        s = copy.deepcopy(TESTED); s['places']['g1_wall_location_D'] = {'area': 'g1_simulated_wall', 'targets': []}   # D has no place
        with self.assertRaisesRegex(ValueError, 'no declared place has the alias'):
            plan(s, CAT)
        s = copy.deepcopy(TESTED)                                                          # D declared for two places
        s['places']['g1_wall_location_D_copy'] = copy.deepcopy(s['places']['g1_wall_location_D'])
        with self.assertRaisesRegex(ValueError, 'ambiguous alias'):
            plan(s, CAT)
        s = copy.deepcopy(TESTED); del s['places']['ur3_handover_point']                   # A task location not declared
        for slot in s['slots']:
            del s['conditions'][slot]['locations']['ur3_handover_point']
        with self.assertRaisesRegex(ValueError, 'not a declared place'):
            plan(s, CAT)
        s = copy.deepcopy(TESTED)                                                          # Conditions for an undeclared place
        s['conditions']['slot-1']['locations']['nowhere'] = {'safety': 'PASS', 'contact_permission': 'PASS'}
        with self.assertRaisesRegex(ValueError, 'not a declared place'):
            plan(s, CAT)
        d_alias = dict(self.G1, target='D')          # Each malformed record keeps D's real alias, so only its own defect can refuse it
        for bad, why in (({'area': 'g1_simulated_wall', 'targets': [d_alias, {'domain': 'simulation', 'target': 'D3'}]}, 'every alias gives'),
                         ({'area': 'g1_simulated_wall', 'targets': 'D'}, 'malformed record'),
                         ({'area': 'g1_simulated_wall', 'aliases': [d_alias]}, 'malformed record'),
                         ({'targets': [d_alias]}, 'malformed record'),                                    # No area
                         ({'area': 'g1_simulated_wall', 'targets': [d_alias], 'colour': 'red'}, 'malformed record'),   
                         ({'area': 'g1_simulated_wall', 'targets': [d_alias, dict(d_alias, target='   ')]}, 'every alias gives'),  
                         ({'area': 'g1_simulated_wall', 'targets': [d_alias, dict(d_alias, domain='physical', target='D2')]}, 'mix domains')):
            s = copy.deepcopy(TESTED); s['places']['g1_wall_location_D'] = bad
            with self.assertRaisesRegex(ValueError, why, msg=repr(bad)):
                plan(s, CAT)
        s = copy.deepcopy(TESTED)                                                          # Undeclared place in slot-2 only
        s['conditions']['slot-2']['locations']['nowhere'] = {'safety': 'PASS', 'contact_permission': 'PASS'}
        with self.assertRaisesRegex(ValueError, 'not a declared place'):
            plan(s, CAT)
        s = copy.deepcopy(TESTED); s['conditions']['slot-3'] = copy.deepcopy(s['conditions']['slot-2'])   # An undeclared slot
        with self.assertRaisesRegex(ValueError, 'exactly the slots'):
            plan(s, CAT)
        for field, value in (('allowed_targets', {'A': 1}), ('allowed_targets', 'D'), ('domain', ['simulation'])):
            s = copy.deepcopy(TESTED); task_of(s, 'T-WS-D')['request'][field] = value
            with self.assertRaisesRegex(ValueError, 'must be text', msg=field):
                plan(s, CAT)
        s = copy.deepcopy(TESTED)                                     # A manual alternative cannot name another place
        task_of(s, 'T-WS-D')['manual_alternative'] = dict(MANUAL_WIPE, location='g1_wall_location_A')
        with self.assertRaisesRegex(ValueError, 'unknown keys'):
            plan(s, CAT)
        s = copy.deepcopy(TESTED); s.pop('places')
        with self.assertRaisesRegex(ValueError, 'no place register'):
            plan(s, CAT)

    def test_domain_and_context_scope_each_alias(self):
        s = copy.deepcopy(TESTED)          # A physical corridor spot also called D: a different place in another domain/context
        s['places']['corridor_spot_D'] = {'area': 'customer_corridor', 'targets': [{'domain': 'physical', 'context': 'customer_corridor_painted_wall', 'target': 'D'}]}
        s['places']['physical_mock_wall_D'] = {'area': 'mock_wall',                        # Same context and name, other domain
                                               'targets': [{'domain': 'physical', 'context': 'supported_original_wall_point_pilot', 'target': 'D'}]}
        for slot in s['slots']:
            s['conditions'][slot]['locations']['corridor_spot_D'] = {'safety': 'FAIL', 'contact_permission': 'FAIL'}
        self.assertEqual(states(plan(s, CAT, declarations=DECL)), states(plan(TESTED, CAT, declarations=DECL)))
        for slot in s['slots']:                                                 # The move is area-consistent, so only the alias decides
            s['conditions'][slot]['areas']['customer_corridor'] = 'PASS'
        task_of(s, 'T-WS-D')['area'] = 'customer_corridor'
        task_of(s, 'T-WS-D')['location'] = 'corridor_spot_D'                    # The simulated D is not the corridor's D
        with self.assertRaisesRegex(ValueError, 'names place g1_wall_location_D but its location is corridor_spot_D'):
            plan(s, CAT)
        s = copy.deepcopy(TESTED); task_of(s, 'T-WS-D')['request']['context'] = 'another_wall'   # Same bare name, other context
        with self.assertRaisesRegex(ValueError, 'no declared place has the alias'):
            plan(s, CAT)

    def test_the_nominal_UR3_UR5_and_G1_aliases_resolve(self):
        from job_plan import place_register, place_of
        for scenario in (BUILDING, TESTED):
            index = place_register(scenario)
            for t in scenario['tasks']:
                if t.get('request') is not None:
                    self.assertEqual(place_of(index, t['request']), t['location'], t['id'])
        index = place_register(TESTED)
        resolved = {t['id']: place_of(index, t['request']) for t in TESTED['tasks']}
        self.assertEqual(resolved, {'T-WB-L': 'ur5_tested_whiteboard', 'T-WS-D': 'g1_wall_location_D', 'T-WS-AB': 'g1_wall_between_A_and_B',
                                    'T-SH': 'ur3_handover_point', 'T-WB-W': 'ur5_tested_whiteboard'})

    def test_a_changed_association_does_not_keep_an_approval(self):
        base = isolated_d(8.0, 8.0)
        decl = new_declaration(base, confirm=('G1_D_8N', 'H1'))
        self.assertEqual(by_id(plan(base, CAT, declarations=decl))['T-WS-D']['state'], 'RECOMMENDED')        
        s = copy.deepcopy(base); s['places']['g1_wall_location_D']['targets'].append(dict(self.G1, target='D_panel'))
        row = by_id(plan(s, CAT, declarations=decl))['T-WS-D']
        self.assertEqual((row['state'], row['synthetic']), ('PENDING_CONFIRMATION', []))
        self.assertIn('job context changed', ' '.join(row['notes']))
        s = copy.deepcopy(base)                                         # Alias moved to another place, location left behind
        s['places']['g1_wall_location_E'] = {'area': 'g1_simulated_wall', 'targets': s['places']['g1_wall_location_D']['targets']}
        s['places']['g1_wall_location_D'] = {'area': 'g1_simulated_wall', 'targets': []}
        with self.assertRaisesRegex(ValueError, 'names place g1_wall_location_E'):
            plan(s, CAT, declarations=decl)


    def test_a_task_area_must_be_its_place_area(self):                                          
        s = isolated_d(8.0, 8.0)
        for slot in s['slots']:
            s['conditions'][slot]['areas']['g1_simulated_wall'] = 'FAIL'
        r = by_id(plan(s, CAT))['T-WS-D']
        self.assertEqual((r['state'], r['robot'], r['person']), ('BLOCKED_SHARED', None, None))        # Control: its own area
        s['tasks'][0]['area'] = 'ur3_cell'                                               # The UR3 cell's PASS borrowed
        with self.assertRaisesRegex(ValueError, 'its area is ur3_cell but its place g1_wall_location_D is in area g1_simulated_wall'):
            plan(s, CAT)
        b = copy.deepcopy(BUILDING); gp1 = task_of(b, 'GP1'); gp1.pop('request')           # A manual-only task
        gp1['contact'] = True
        for slot in b['slots']:
            b['conditions'][slot]['areas']['glass_partitions'] = 'FAIL'
        self.assertEqual((by_id(plan(b, CAT))['GP1']['state'], by_id(plan(b, CAT))['GP1']['person']), ('BLOCKED_SHARED', None))
        gp1['area'] = 'corridor'
        with self.assertRaisesRegex(ValueError, 'its area is corridor'):
            plan(b, CAT)
        s = isolated_d(8.0, 8.0); s['tasks'][0]['area'] = 'nowhere'
        with self.assertRaisesRegex(ValueError, 'has no declared condition'):
            plan(s, CAT)
        base = isolated_d(8.0, 8.0); decl = new_declaration(base, confirm=('G1_D_8N', 'H1'))
        moved = copy.deepcopy(base); moved['tasks'][0]['area'] = 'ur3_cell'; moved['places']['g1_wall_location_D']['area'] = 'ur3_cell'
        row = by_id(plan(moved, CAT, declarations=decl))['T-WS-D']                        # A consistent move: context changed
        self.assertEqual((row['state'], row['synthetic']), ('PENDING_CONFIRMATION', []))


class HumanRoles(unittest.TestCase):                                                                  
    """Check the distinction between starting and watching, confirming, and commanding.
    Each task must name the people its recorded mode requires."""

    def test_the_ur5_detection_reserves_its_attendant_and_lists_the_action(self):
        for scenario, decl in ((TESTED, None), (TESTED, DECL)):
            result = plan(scenario, CAT, declarations=decl)
            r = by_id(result)['T-WB-L']
            self.assertEqual((r['state'], r['robot']['configuration'], r['person'], r['person_role']),
                             ('RECOMMENDED', 'UR5_whiteboard_detection', 'H1', ['attendant']))
            self.assertEqual(r['selector']['decision'], 'SELECT')          # No per-target confirmation in the demonstrated mode
            actions = [(x['task'], x['action'], x['by']) for x in result['human_actions'] if x['task'] == 'T-WB-L']
            self.assertEqual(actions, [('T-WB-L', 'set-up, start and observation', 'H1')])
            self.assertEqual(result['reservations']['people']['slot-1:H1'], 'T-WB-L')
        entry = ROLES['attended_start_automatic_detection']
        self.assertEqual((entry['roles'], entry['per_target_confirmation']), (['attendant'], False))

    def test_without_a_free_attendant_the_robot_is_not_reserved(self):
        s = copy.deepcopy(TESTED); s['team']['people'][0]['availability']['slot-1'] = 'UNAVAILABLE'
        result = plan(s, CAT); r = by_id(result)
        self.assertEqual((r['T-WB-L']['state'], r['T-WB-L']['robot'], r['T-WB-L']['person'], r['T-WB-L']['route']),
                         ('WAITING_FOR_PERSON', None, None, 'robot'))          # H2 holds no role and is never used
        self.assertNotIn('slot-1:UR5', result['reservations']['robots'])
        self.assertEqual((r['T-WB-W']['state'], r['T-WB-W']['inputs'][0]['status']), ('CONDITIONAL_ON_INPUT', 'awaiting'))
        s['team']['people'][1]['roles'] = ['attendant']                        # A second declared attendant
        r = by_id(plan(s, CAT))['T-WB-L']
        self.assertEqual((r['state'], r['person'], r['person_role']), ('RECOMMENDED', 'H2', ['attendant']))
        s = copy.deepcopy(TESTED); s['team']['people'][0]['roles'] = ['confirmer', 'operator']   # Nobody holds the role
        self.assertEqual(by_id(plan(s, CAT))['T-WB-L']['state'], 'WAITING_FOR_PERSON')

    def test_a_sub_task_must_declare_the_roles_of_its_mode(self):
        for scenario, tid, role in ((TESTED, 'T-WB-L', 'attendant'), (TESTED, 'T-WS-D', 'operator'), (TESTED, 'T-WS-AB', 'operator'),
                                    (TESTED, 'T-SH', 'operator'), (BUILDING, 'WB2-L', 'attendant'), (BUILDING, 'SH1', 'operator')):
            for roles in (None, [], ['confirmer']):
                s = copy.deepcopy(scenario)
                if roles is None:
                    task_of(s, tid).pop('robot_roles')
                else:
                    task_of(s, tid)['robot_roles'] = roles
                with self.assertRaisesRegex(ValueError, r"%s: mode \S+ was demonstrated with the human role\(s\) \['%s'\]" % (tid, role),
                                            msg=(tid, roles)):
                    plan(s, CAT)
        for bad in ('attendant', ['attendant', ''], [None]):
            s = copy.deepcopy(TESTED); task_of(s, 'T-WB-L')['robot_roles'] = bad
            with self.assertRaisesRegex(ValueError, 'robot_roles must be a list of role names', msg=repr(bad)):
                plan(s, CAT)
        s = copy.deepcopy(TESTED); task_of(s, 'T-WB-L')['robot_roles'] = ['attendant', 'operator']   # More roles may be declared
        r = by_id(plan(s, CAT))['T-WB-L']
        self.assertEqual((r['state'], r['person'], r['person_role']), ('RECOMMENDED', 'H1', ['attendant', 'operator']))

    def test_an_unknown_human_role_is_never_treated_as_none(self):
        unknown = {k: v for k, v in ROLES.items() if k != 'attended_start_automatic_detection'}
        with self.assertRaisesRegex(ValueError, 'no human role is declared for mode attended_start_automatic_detection'):
            plan(TESTED, CAT, human_roles=unknown)
        with self.assertRaisesRegex(ValueError, 'an unknown role is not treated as none'):
            plan(BUILDING, CAT, human_roles=unknown)                        # Even where the record is out of scope for other reasons
        cat = copy.deepcopy(CAT); ur5 = next(c for c in cat if c['id'] == 'UR5_whiteboard_detection')
        ur5['mode'] = 'reported_static_detection'                           # The historical label, which declares no human role
        s = copy.deepcopy(TESTED); task_of(s, 'T-WB-L')['request']['allowed_modes'] = ['reported_static_detection']
        with self.assertRaisesRegex(ValueError, 'no human role is declared for mode reported_static_detection'):
            plan(s, cat)
        control = by_id(plan(TESTED, CAT, human_roles=ROLES))               # The declared table: the nominal plan
        self.assertEqual(states(plan(TESTED, CAT)), {k: (v['state'], v['robot'] and v['robot']['configuration'], v['person'])
                                                     for k, v in control.items()})

    def test_the_confirmation_requirement_must_agree_with_the_record(self):
        for mode, profile in (('attended_start_automatic_detection', 'UR5_whiteboard_detection'),
                              ('virtual_confirmation_then_bounded_wipe', 'G1_D_7N'),
                              ('explicit_command_confirmation_STOP', 'UR3_registered_handover')):
            table = copy.deepcopy(ROLES); table[mode]['per_target_confirmation'] = not table[mode]['per_target_confirmation']
            with self.assertRaisesRegex(ValueError, 'declares per-target confirmation', msg=mode):
                plan(TESTED, CAT, human_roles=table)
            self.assertEqual(ROLES[mode]['per_target_confirmation'], next(c for c in CAT if c['id'] == profile)['confirmation_required'])
        self.assertEqual(set(ROLES), {c['mode'] for c in CAT})                   # Every recorded mode declares its human role

    def test_malformed_role_tables_are_rejected(self):
        import tempfile
        good = json.loads((ROOT / 'catalogue_r4/CANDIDATE_MAPPING_R4.json').read_text())
        mode = 'attended_start_automatic_detection'
        def table(change):
            doc = copy.deepcopy(good); change(doc)
            with tempfile.TemporaryDirectory(prefix='job-roles-') as tmp:
                path = Path(tmp) / 'catalogue.json'; path.write_text(json.dumps(doc))
                return load_human_roles(path)
        self.assertEqual(table(lambda d: None), ROLES)                          
        for change, why in ((lambda d: d.pop('human_roles_by_mode'), 'no human roles are declared'),
                            (lambda d: d.update(human_roles_by_mode={}), 'no human roles are declared'),
                            (lambda d: d['human_roles_by_mode'][mode].pop('basis'), 'needs roles'),
                            (lambda d: d['human_roles_by_mode'][mode].update(basis=' '), 'needs roles'),
                            (lambda d: d['human_roles_by_mode'][mode].update(roles='attendant'), 'needs roles'),
                            (lambda d: d['human_roles_by_mode'][mode].update(roles=[]), 'needs roles'),   # 'nobody' cannot be entered silently
                            (lambda d: d['human_roles_by_mode'][mode].update(roles=['attendant', 3]), 'needs roles'),
                            (lambda d: d['human_roles_by_mode'][mode].update(per_target_confirmation='no'), 'needs roles'),
                            (lambda d: d['human_roles_by_mode'][mode].update(colour='red'), 'needs roles')):
            with self.assertRaisesRegex(ValueError, why):
                table(change)

    def test_a_named_confirmer_is_not_replaced_by_another_person(self):
        s = copy.deepcopy(TESTED); s['team']['people'][1]['roles'] = ['confirmer', 'operator']   # H2 could confirm and indicate
        r = by_id(plan(s, CAT, declarations=DECL))['T-WS-D']
        # The declaration names H1, who attends the UR5 detection in slot-1: it is not applied, and H2 has confirmed nothing
        self.assertEqual((r['state'], r['robot']['configuration'], r['person'], r['person_role']),
                         ('PENDING_CONFIRMATION', 'G1_D_8N', 'H2', ['confirmer', 'operator']))
        self.assertEqual(r['synthetic'], ['choice of G1_D_8N by owner'])
        self.assertIn('declared confirmation not applied', ' '.join(r['notes']))

    def test_a_role_table_passed_to_plan_is_checked_like_a_loaded_one(self):                         
        """Require the same input checks for a role table loaded from disk or passed directly."""
        import tempfile
        good = json.loads((ROOT / 'catalogue_r4/CANDIDATE_MAPPING_R4.json').read_text())
        mode = 'attended_start_automatic_detection'

        def loader_refusal(table):
            doc = copy.deepcopy(good); doc['human_roles_by_mode'] = table
            with tempfile.TemporaryDirectory(prefix='job-roles-') as tmp:
                path = Path(tmp) / 'catalogue.json'; path.write_text(json.dumps(doc))
                with self.assertRaises(ValueError) as refused:
                    load_human_roles(path)
            return str(refused.exception)

        def changed(change):
            t = copy.deepcopy(ROLES); change(t); return t
        cases = {'an empty table': {},
                 'an empty role list': changed(lambda t: t[mode].update(roles=[])),
                 'a missing basis': changed(lambda t: t[mode].pop('basis')),
                 'a blank basis': changed(lambda t: t[mode].update(basis=' ')),
                 'roles that are not a list': changed(lambda t: t[mode].update(roles='attendant')),
                 'a role name that is not text': changed(lambda t: t[mode].update(roles=['attendant', 3])),
                 'a blank role name': changed(lambda t: t[mode].update(roles=['attendant', ' '])),
                 'a confirmation that is not true or false': changed(lambda t: t[mode].update(per_target_confirmation=0)),
                 'a missing confirmation': changed(lambda t: t[mode].pop('per_target_confirmation')),
                 'an unknown key': changed(lambda t: t[mode].update(colour='red')),
                 'an entry that is not a table': changed(lambda t: t.update({mode: ['attendant']})),
                 'a blank mode name': changed(lambda t: t.update({' ': copy.deepcopy(t[mode])}))}
        for name, table in cases.items():
            expected = loader_refusal(table)
            for scenario, decl in ((TESTED, None), (TESTED, DECL), (BUILDING, None)):
                with self.assertRaises(ValueError, msg=name) as refused:
                    plan(scenario, CAT, declarations=decl, human_roles=table)
                self.assertEqual(str(refused.exception), expected, name)          # The same refusal through both routes
        for name, table in (('a list of entries', list(ROLES.items())),           # Structures that JSON cannot carry
                            ('a mode name that is not text', changed(lambda t: t.update({3: copy.deepcopy(t[mode])}))),
                            ('a tuple as mode name', changed(lambda t: t.update({(1, 2): copy.deepcopy(t[mode])}))),
                            ('an empty tuple as mode name', changed(lambda t: t.update({(): copy.deepcopy(t[mode])})))):
            with self.assertRaisesRegex(ValueError, 'no human roles are declared|needs roles, per_target_confirmation and a basis', msg=name):
                plan(TESTED, CAT, human_roles=table)
        for scenario, decl in ((BUILDING, None), (TESTED, None), (TESTED, DECL)):     # Control: a well-formed copy gives the nominal plans
            self.assertEqual(plan(scenario, CAT, declarations=decl, human_roles=copy.deepcopy(ROLES)),
                             plan(scenario, CAT, declarations=decl))


class Regression(unittest.TestCase):
    def test_simulation_profiles_do_not_change_the_customer_building_plan(self):                       
        a = states(plan(BUILDING, CAT))
        s = copy.deepcopy(BUILDING); s['team']['robots'] = ['UR3', 'UR5']
        self.assertEqual(a, states(plan(s, CAT)))                                   # G1 not in the team
        s['live']['platform_availability'].pop('G1')
        self.assertEqual(a, states(plan(s, [c for c in CAT if c['platform'] != 'G1'])))   # G1 profiles removed

    def test_frozen_catalogue_changes_only_the_site_D_task(self):                                     
        # Remove only the 8 N records; keep the UR5 human-role assessment unchanged.
        now, earlier = by_id(plan(TESTED, CAT)), by_id(plan(TESTED, EARLIER))
        view = lambda rows: {k: (v['state'], v['selector'] and (v['selector']['decision'], v['selector']['configuration'],
                                                               v['selector']['remaining'])) for k, v in rows.items()}
        a, b = view(now), view(earlier)
        self.assertEqual({k for k in a if a[k] != b[k]}, {'T-WS-D'})
        self.assertEqual(a['T-WS-D'], ('PENDING_CHOICE', ('ASK', None, ['G1_D_7N', 'G1_D_8N'])))
        self.assertEqual(b['T-WS-D'][1][:2], ('CONFIRM', 'G1_D_7N'))          # The rule's outcome with 7 N records only
        self.assertEqual(b['T-WS-D'][0], 'WAITING_FOR_PERSON')                # H1 attends the UR5 detection in slot-1
        free = without_ur5_tasks(TESTED)                                      # With H1 free the 7 N option is reserved
        r = by_id(plan(free, EARLIER))['T-WS-D']
        self.assertEqual((r['state'], r['robot']['configuration'], r['person']), ('PENDING_CONFIRMATION', 'G1_D_7N', 'H1'))
        self.assertEqual(states(plan(BUILDING, CAT)), states(plan(BUILDING, EARLIER)))
        # With the synthetic declarations, which are bound to the register with the 8 N records, two rows differ: the choice of
        # 8 N names no remaining option, and the handover's confirmation is stale because its binding covers the register
        a, b = view(by_id(plan(TESTED, CAT, declarations=DECL))), view(by_id(plan(TESTED, EARLIER, declarations=DECL)))
        self.assertEqual({k for k in a if a[k] != b[k]}, {'T-WS-D', 'T-SH'})
        self.assertEqual((a['T-SH'][0], b['T-SH'][0]), ('RECOMMENDED', 'PENDING_CONFIRMATION'))
        self.assertEqual((b['T-WS-D'][0], b['T-WS-D'][1][:2]), ('WAITING_FOR_PERSON', ('CONFIRM', 'G1_D_7N')))
        self.assertEqual([c['id'] for c in EARLIER], [c['id'] for c in FROZEN])   # The same six records as the historical catalogue

    def test_selector_bytes_unchanged(self):                                                           
        self.assertEqual(hashlib.sha256((ROOT / 'selector/selector.py').read_bytes()).hexdigest(), SELECTOR_SHA256)


class ReviewProbes(unittest.TestCase):
    """Check invalid teams, stale declarations and missing human roles."""

    def test_r1_empty_team_recommends_nothing(self):
        s = copy.deepcopy(TESTED); s['team']['robots'] = []
        r = by_id(plan(s, CAT, declarations=DECL))
        self.assertFalse(any(v['state'] in ('RECOMMENDED', 'PENDING_CONFIRMATION', 'PENDING_CHOICE') for v in r.values()))

    def test_r2_original_declaration_after_changed_contents(self):
        a = isolated_d(8.0, 8.0)
        decl = new_declaration(a, confirm=('G1_D_8N', 'H1'))
        b = copy.deepcopy(a); b['tasks'][0]['request']['force']['minimum_n'] = 7.9
        c = copy.deepcopy(a); c['live']['platform_availability']['UR5'] = 'BUSY'
        cat = copy.deepcopy(CAT); next(p for p in cat if p['id'] == 'G1_D_8N')['assessment_basis'] += ' [Review updated.]'
        for scenario, catalogue in ((b, CAT), (c, CAT), (a, cat)):
            self.assertEqual(by_id(plan(scenario, catalogue, declarations=decl))['T-WS-D']['state'], 'PENDING_CONFIRMATION')

    def test_r3_location_hazard_cannot_be_lost(self):
        legacy = isolated_d(8.0, 8.0); legacy['tasks'][0]['manual_alternative'] = dict(MANUAL_WIPE)
        legacy['live']['candidate_overrides'] = {'G1_D_8N': {'safety': 'FAIL'}}
        with self.assertRaises(ValueError):                  # Refuse location conditions supplied as per-candidate checks.
            plan(legacy, CAT)
        for minimum, maximum in ((8.0, 8.0), (9.0, 9.0), (6.0, 8.0)):
            s = isolated_d(minimum, maximum); s['tasks'][0]['manual_alternative'] = dict(MANUAL_WIPE)
            set_location(s, 'T-WS-D', safety='FAIL')
            r = by_id(plan(s, CAT))['T-WS-D']
            self.assertEqual((r['state'], r['person'], r['robot']), ('BLOCKED_TASK_CONDITION', None, None), (minimum, maximum))

    def test_n1_waiting_operator_keeps_consumer_conditional(self):
        s = copy.deepcopy(TESTED)                  # The UR5 task requires an attendant.
        s['team']['people'][0]['availability']['slot-1'] = 'BUSY'
        self.assertEqual(by_id(plan(s, CAT))['T-WB-W']['state'], 'CONDITIONAL_ON_INPUT')

    def test_r31_role_table_probe_through_plan(self):                                                  
        """Refuse an empty role list or missing basis before planning a UR5 task without a person."""
        mode = 'attended_start_automatic_detection'
        s = copy.deepcopy(TESTED); s['tasks'] = [task_of(s, 'T-WB-L')]; s['team']['people'] = []
        bare = copy.deepcopy(s); task_of(bare, 'T-WB-L')['robot_roles'] = []
        for table in (None, copy.deepcopy(ROLES)):                     # Controls: the default and a well-formed supplied table
            r = by_id(plan(s, CAT, human_roles=table))['T-WB-L']
            self.assertEqual((r['state'], r['robot'], r['person'], r['route']), ('WAITING_FOR_PERSON', None, None, 'robot'))
            with self.assertRaisesRegex(ValueError, r"T-WB-L: mode %s was demonstrated with the human role\(s\) \['attendant'\]" % mode):
                plan(bare, CAT, human_roles=table)                    # A sub-task without its attendant is still refused
        empty = copy.deepcopy(ROLES); empty[mode]['roles'] = []
        no_basis = copy.deepcopy(ROLES); del no_basis[mode]['basis']
        for scenario, table in ((bare, empty), (s, empty), (s, no_basis)):
            with self.assertRaisesRegex(ValueError, r'human_roles_by_mode\[%s\] needs roles, per_target_confirmation and a basis' % mode):
                plan(scenario, CAT, human_roles=table)


if __name__ == '__main__':
    unittest.main(verbosity=2)
