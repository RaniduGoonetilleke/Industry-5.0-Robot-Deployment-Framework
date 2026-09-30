"""Build a conditional job plan from the declared team, tasks and conditions.

Each robot request is passed to the selector. People and robots are reserved
within logical slots, which have no duration. The scenarios are authored inputs.
This code checks a proposed plan. It does not schedule times or dispatch work."""
import copy
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]          
sys.path.insert(0, str(ROOT / 'selector'))
from selector import select, digest, LIVE            # noqa: E402
from build_examples import live_for                  # noqa: E402

CATALOGUE = ROOT / 'catalogue_r4' / 'CANDIDATE_MAPPING_R4.json'     # Catalogue including the UR5 attendant role.
STATES = ('RECOMMENDED', 'PENDING_CONFIRMATION', 'PENDING_CHOICE', 'WAITING', 'WAITING_FOR_PERSON', 'MANUAL_ASSIGNED',
          'CONDITIONAL_ON_INPUT', 'BLOCKED_INPUT', 'BLOCKED_SHARED', 'WITHHELD_SHARED', 'BLOCKED_TASK_CONDITION',
          'WITHHELD_TASK_CONDITION', 'BLOCKED_EVIDENCE', 'BLOCKED_NOT_IN_TEAM', 'BLOCKED_ROBOT_CHECK', 'WITHHELD_ROBOT_CHECK')
CONDITION_STATES = ('PASS', 'FAIL', 'UNKNOWN', 'CONFLICTING')
# Safety and contact permission belong to the task location and slot.
# Apply them to every team option considered for this task.
TASK_LEVEL = ('live:safety', 'live:contact_authorized')
CONTACT_CAPABILITIES = tuple(sorted(c for c, checks in LIVE.items() if 'contact_authorized' in checks))
GAP_FIELDS = ('domain', 'context', 'target', 'mode', 'evidence_level', 'setpoint', 'settings')
ROBOT_ROUTE = ('RECOMMENDED', 'PENDING_CONFIRMATION', 'PENDING_CHOICE', 'WAITING', 'WAITING_FOR_PERSON')
HEX64 = re.compile(r'[0-9a-f]{64}')


def load_catalogue(path=CATALOGUE):
    return json.loads(Path(path).read_text())['profiles']


def validate_human_roles(table):
    """Check the role table whether loaded from a file or supplied directly.
    
    Each mode needs a nonempty role list, a confirmation flag and a stated basis.
    Malformed or empty entries raise ValueError, and missing information is not filled in."""
    if type(table) is not dict or not table:
        raise ValueError('no human roles are declared (human_roles_by_mode is missing, empty or not a table)')
    for mode, entry in table.items():
        if type(mode) is not str or not mode.strip() \
                or type(entry) is not dict or set(entry) != {'roles', 'per_target_confirmation', 'basis'} \
                or type(entry['roles']) is not list or not entry['roles'] \
                or not all(type(r) is str and r.strip() for r in entry['roles']) \
                or type(entry['per_target_confirmation']) is not bool \
                or type(entry['basis']) is not str or not entry['basis'].strip():
            raise ValueError('human_roles_by_mode[%s] needs roles, per_target_confirmation and a basis' % (mode,))
    return table


def load_human_roles(path=CATALOGUE):
    """Load the human roles required by each recorded mode."""
    return validate_human_roles(json.loads(Path(path).read_text()).get('human_roles_by_mode'))


def team_catalogue(scenario, catalogue):
    """Return only profiles belonging to the declared robot team."""
    robots = scenario['team'].get('robots')
    if type(robots) is not list or not all(type(r) is str and r.strip() for r in robots):
        raise ValueError('team.robots must be a list of platform names')
    if len(set(robots)) != len(robots):
        raise ValueError('team.robots lists a platform more than once')
    unknown = sorted(set(robots) - {c['platform'] for c in catalogue})
    if unknown:
        raise ValueError('team.robots names platforms with no capability record: %s' % unknown)
    return [c for c in catalogue if c['platform'] in robots]


def _live_facts(catalogue, scenario):
    team = team_catalogue(scenario, catalogue)
    live = live_for(team, 'PASS')
    decl = scenario['live']
    live['snapshot_id'] = decl['snapshot_id']
    platforms, ids, unused = {c['platform'] for c in catalogue}, {c['id'] for c in catalogue}, []
    for field, key in (('platform_availability', 'availability'), ('platform_safety', 'safety')):
        for name, state in decl.get(field, {}).items():
            if name not in platforms:
                raise ValueError('live.%s names an unknown platform %s' % (field, name))
            if name in live['platforms']:
                live['platforms'][name][key] = state
            else:
                unused.append('%s.%s (not in the team)' % (field, name))
    for cid, override in decl.get('candidate_overrides', {}).items():
        if cid not in ids:
            raise ValueError('live.candidate_overrides names an unknown candidate %s' % cid)
        if set(override) != {'checks'} or 'contact_authorized' in override['checks']:
            raise ValueError('%s: local safety and contact permission are task-location conditions; declare them under '
                             'conditions[slot].locations, not per candidate' % cid)
        if cid in live['candidates']:
            live['candidates'][cid]['checks'].update(override['checks'])
        else:
            unused.append('candidate_overrides.%s (not in the team)' % cid)
    return live, unused


def base_live(catalogue, scenario):
    """Build the scenario's synthetic live state for the team.
    
    Robot checks default to PASS unless overridden. Location safety and contact
    permission are applied separately for each task."""
    return _live_facts(catalogue, scenario)[0]


def evidence_gap(request, result, catalogue):
    """List the recorded fields that differ from the request, for candidates of the requested capability (record vs requested)."""
    requested = {'domain': request['domain'], 'context': request['context'], 'target': request['allowed_targets'],
                 'mode': request['allowed_modes'], 'evidence_level': request['accepted_evidence_levels'],
                 'setpoint': [request['force']['minimum_n'], request['force']['maximum_n']] if request['force'] else None,
                 'settings': request['required_settings']}
    gap = []
    for c in catalogue:
        if c['capability'] != request['capability'] or c['id'] not in result['not_applicable']:
            continue
        fields = [f for f in result['not_applicable'][c['id']] if f in GAP_FIELDS]
        record = {'domain': c['domain'], 'context': c['context'], 'target': c['target'], 'mode': c['mode'],
                  'evidence_level': c['evidence_level'], 'setpoint': c['force_setpoint_n'], 'settings': c['settings']}
        gap.append({'candidate': c['id'], 'mismatch': {f: {'record': record[f], 'requested': requested[f]} for f in fields}})
    if not any(c['capability'] == request['capability'] for c in catalogue):
        gap.append({'candidate': None, 'mismatch': {'capability': {'record': None, 'requested': request['capability']}}})
    return gap


def _person(team, occupied, slot, *, roles=(), training=None, only=None):
    for p in team['people']:
        if only is not None and p['id'] != only:
            continue
        if p['availability'].get(slot) != 'AVAILABLE' or (slot, p['id']) in occupied:
            continue
        if not set(roles) <= set(p['roles']):
            continue
        if training is not None and training not in p['training']:
            continue
        return p['id']
    return None


def _why_not(team, occupied, slot, roles, name):
    """Give the first applicable reason why the named person cannot take these roles in this slot, for the plan's note."""
    found = [q for q in team['people'] if q['id'] == name]
    if not found:
        return '%s is not a declared person of the team' % name
    lacking = sorted(set(roles) - set(found[0]['roles']))
    if lacking:
        return '%s does not hold the role(s) %s' % (name, ', '.join(lacking))
    if found[0]['availability'].get(slot) != 'AVAILABLE':
        return '%s is not available in %s' % (name, slot)
    return '%s is occupied in %s by %s' % (name, slot, occupied[(slot, name)])


def involves_contact(task):
    request = task.get('request')
    return bool((request is not None and request['capability'] in CONTACT_CAPABILITIES) or task.get('contact')
                or (task.get('manual_alternative') or {}).get('contact'))


def place_register(scenario):
    """Check the declared place register and return its alias index. Each alias (domain, context, target) names exactly one
        place. Malformed or ambiguous registers raise ValueError. This checks only authored consistency and says nothing about
        safety in the world."""
    places = scenario.get('places')
    if type(places) is not dict or not places:
        raise ValueError('the scenario declares no place register (places)')
    index = {}
    for pid, place in places.items():
        if type(pid) is not str or not pid.strip() or type(place) is not dict \
                or not {'area', 'targets'} <= set(place) <= {'area', 'targets', 'description'} \
                or type(place['area']) is not str or not place['area'].strip() or type(place['targets']) is not list:
            raise ValueError('place %r: malformed record (it needs an area and a list of target aliases)' % pid)
        if len({a.get('domain') for a in place['targets'] if type(a) is dict}) > 1:
            raise ValueError('place %s: its aliases mix domains' % pid)
        for alias in place['targets']:
            if type(alias) is not dict or set(alias) != {'domain', 'context', 'target'} or \
                    not all(type(v) is str and v.strip() for v in alias.values()):
                raise ValueError('place %s: every alias gives a domain, a context and a target' % pid)
            key = (alias['domain'], alias['context'], alias['target'])
            if index.get(key, pid) != pid:
                raise ValueError('ambiguous alias %s: declared for places %s and %s' % (key, index[key], pid))
            index[key] = pid
    return index


def place_of(index, request):
    """The declared place of a robot request's single target, scoped by its domain and context (None if undeclared)."""
    return index.get((request['domain'], request['context'], request['allowed_targets'][0]))


def validate_scenario(scenario, catalogue):
    """Check the declared team, task locations, conditions and outputs. Every declared input must name an output its
    producer declares (by its robot route or its manual alternative)."""
    team_ids = {c['id'] for c in team_catalogue(scenario, catalogue)}
    ids = [t['id'] for t in scenario['tasks']]
    if len(set(ids)) != len(ids):
        raise ValueError('task IDs must be distinct')
    tasks = {t['id']: t for t in scenario['tasks']}
    slots = scenario['slots']
    index = place_register(scenario)
    if set(scenario['conditions']) != set(slots):
        raise ValueError('conditions must be declared for exactly the slots %s' % slots)
    for slot in slots:
        for loc in scenario['conditions'][slot].get('locations', {}):
            if loc not in scenario['places']:
                raise ValueError('conditions in %s name %s, which is not a declared place' % (slot, loc))
    for t in scenario['tasks']:
        cond = scenario['conditions'][t['slot']]
        if t['area'] not in cond['areas']:
            raise ValueError('%s: area %s has no declared condition in %s' % (t['id'], t['area'], t['slot']))
        if cond['shared_safety'] not in CONDITION_STATES or cond['areas'][t['area']] not in CONDITION_STATES:
            raise ValueError('%s: shared and area conditions must be one of %s' % (t['id'], CONDITION_STATES))
        if type(t.get('location')) is not str or not t['location'].strip():
            raise ValueError('%s declares no location' % t['id'])
        if t['location'] not in scenario['places']:
            raise ValueError('%s: location %s is not a declared place' % (t['id'], t['location']))
        if scenario['places'][t['location']]['area'] != t['area']:        # Use the authorisation for this place's own area.
            raise ValueError('%s: its area is %s but its place %s is in area %s' % (
                t['id'], t['area'], t['location'], scenario['places'][t['location']]['area']))
        loc = cond.get('locations', {}).get(t['location'])
        if type(loc) is not dict or set(loc) != {'safety', 'contact_permission'} or \
                not all(v in CONDITION_STATES for v in loc.values()):
            raise ValueError('%s: %s needs safety and contact_permission in %s' % (t['id'], t['location'], t['slot']))
        if t.get('request') is None and type(t.get('contact')) is not bool:
            raise ValueError('%s: a manual-only task must declare contact (true or false)' % t['id'])
        if 'contact' in t and type(t['contact']) is not bool:
            raise ValueError('%s: contact must be true or false' % t['id'])
        if t.get('manual_alternative') is not None and type(t['manual_alternative'].get('contact')) is not bool:
            raise ValueError('%s: a manual alternative must declare contact (true or false)' % t['id'])
        if t.get('manual_alternative') is not None and \
                not set(t['manual_alternative']) <= {'mode', 'criteria', 'training', 'produces', 'contact'}:
            raise ValueError('%s: a manual alternative has unknown keys; it works at its task\'s location' % t['id'])
        if t.get('request') is None and 'produces' in t:
            raise ValueError('%s: a manual-only task declares its outputs under manual_alternative.produces' % t['id'])
        if t.get('request') is not None:
            r = t['request']
            if type(r) is not dict or not all(type(r.get(k)) is str and r[k].strip() for k in ('domain', 'context')) \
                    or type(r.get('allowed_targets')) is not list \
                    or not all(type(x) is str and x.strip() for x in r['allowed_targets']):
                raise ValueError('%s: the request\'s domain, context and allowed targets must be text' % t['id'])
            if len(t['request']['allowed_targets']) != 1:     # Each robot task has one target and its own location conditions.
                raise ValueError('%s allows several targets; declare one sub-task per target and location' % t['id'])
            place = place_of(index, t['request'])             # The target and the conditions must describe the same place
            if place is None:
                raise ValueError('%s: no declared place has the alias (%s, %s, %s)' % (
                    t['id'], t['request']['domain'], t['request']['context'], t['request']['allowed_targets'][0]))
            if place != t['location']:
                raise ValueError('%s: its target names place %s but its location is %s' % (t['id'], place, t['location']))
            outside = sorted(set(t['request']['candidate_priority']) - team_ids)
            if outside:
                raise ValueError('%s: candidate_priority names profiles outside the declared team: %s' % (t['id'], outside))
        for producer, output in t.get('needs', []):
            if producer not in tasks:
                raise ValueError('%s needs an unknown producer %s' % (t['id'], producer))
            p = tasks[producer]
            declared = set(p.get('produces', [])) | set((p.get('manual_alternative') or {}).get('produces', []))
            if output not in declared:
                raise ValueError('%s needs %s, which %s does not declare' % (t['id'], output, producer))
            if (slots.index(p['slot']), scenario['tasks'].index(p)) >= (slots.index(t['slot']), scenario['tasks'].index(t)):
                raise ValueError('%s must come after its producer %s' % (t['id'], producer))


def validate_roles(scenario, catalogue, human_roles):
    """Check that a robot sub-task declares every human role with which a team profile it could use was demonstrated.
        A mode without a declared human role is an input error, because an unknown role is never treated as no role. This checks
        declared inputs against the register. It does not show that a person is competent or that a mode is safe."""
    for t in scenario['tasks']:
        request = t.get('request')
        if request is None:
            continue
        declared = t.get('robot_roles', [])
        if type(declared) is not list or not all(type(r) is str and r.strip() for r in declared):
            raise ValueError('%s: robot_roles must be a list of role names' % t['id'])
        for c in team_catalogue(scenario, catalogue):
            if c['capability'] != request['capability'] or c['mode'] not in request['allowed_modes']:
                continue
            entry = human_roles.get(c['mode'])
            if entry is None:
                raise ValueError('%s: no human role is declared for mode %s (%s); an unknown role is not treated as none'
                                 % (t['id'], c['mode'], c['id']))
            if entry['per_target_confirmation'] is not c['confirmation_required']:
                raise ValueError('%s: mode %s declares per-target confirmation %s, but %s records %s'
                                 % (t['id'], c['mode'], entry['per_target_confirmation'], c['id'], c['confirmation_required']))
            undeclared = sorted(set(entry['roles']) - set(declared))
            if undeclared:
                raise ValueError('%s: mode %s was demonstrated with the human role(s) %s, which the sub-task does not declare'
                                 % (t['id'], c['mode'], undeclared))


def job_context(scenario, task):
    """Collect the task, place, team and conditions to which a declaration applies."""
    cond = scenario['conditions'][task['slot']]
    return digest({'task': task, 'place': scenario['places'][task['location']], 'shared_safety': cond['shared_safety'],
                   'area': cond['areas'][task['area']], 'location': cond['locations'][task['location']],
                   'team_robots': sorted(scenario['team']['robots'])})


def validate_declarations(scenario, declarations):
    with_request = {t['id'] for t in scenario['tasks'] if t.get('request') is not None}
    for tid, d in declarations.items():
        if tid not in with_request:
            raise ValueError('declaration for %s, which is not a task with a robot request' % tid)
        if type(d) is not dict or not set(d) <= {'snapshot_id', 'job_context', 'choice', 'confirm', 'basis', 'authored_from'}:
            raise ValueError('declaration schema for %s' % tid)
        if type(d.get('job_context')) is not str or not HEX64.fullmatch(d['job_context']):
            raise ValueError('declaration for %s carries no job-context digest' % tid)
        for kind in ('choice', 'confirm'):
            x = d.get(kind)
            if x is not None and (type(x) is not dict or not set(x) <= {'candidate_id', 'by', 'binding'}
                                  or type(x.get('candidate_id')) is not str
                                  or type(x.get('binding')) is not str or not HEX64.fullmatch(x['binding'])):
                raise ValueError('%s %s must carry candidate_id and the original 64-hex selector binding' % (tid, kind))
        if d.get('choice') is not None and not (type(d['choice'].get('by')) is str and d['choice']['by'].strip()):
            raise ValueError('%s choice must name who made it (by)' % tid)


def validate_completions(scenario, completions):
    tasks = {t['id']: t for t in scenario['tasks']}
    for producer, output in completions:
        p = tasks.get(producer)
        declared = set() if p is None else set(p.get('produces', [])) | set((p.get('manual_alternative') or {}).get('produces', []))
        if output not in declared:
            raise ValueError('completion (%s, %s) names no declared output' % (producer, output))


def declaration_from_plan(row, *, snapshot_id, choice=None, confirm=None, basis, authored_from):
    """Create an illustrative declaration from a task's pre-declaration plan.
    
    Copy its binding and job context unchanged. choice and confirm are pairs of
    candidate ID and person. The planner never calls this function itself."""
    d = {'snapshot_id': snapshot_id, 'job_context': row['job_context'], 'basis': basis, 'authored_from': authored_from}
    for kind, value in (('choice', choice), ('confirm', confirm)):
        if value is not None:
            d[kind] = {'candidate_id': value[0], 'by': value[1], 'binding': row['selector']['binding']}
    return d


def can_still_produce(row, task, output):
    """A producer supplies an output only through the route it is on."""
    if row['state'] == 'CONDITIONAL_ON_INPUT':
        return output in task.get('produces', [])
    if row['route'] == 'robot' and row['state'] in ROBOT_ROUTE:
        return output in task.get('produces', [])
    if row['route'] == 'manual' and row['state'] in ('MANUAL_ASSIGNED', 'WAITING_FOR_PERSON'):
        return output in (task.get('manual_alternative') or {}).get('produces', [])
    return False


def _summary(result):
    return {'decision': result['decision'], 'reason': result['reasons'][-1],
            'configuration': result['configuration']['id'] if result['configuration'] else None,
            'remaining': result['remaining'], 'binding': result['binding']}


def plan(scenario, catalogue, *, declarations=None, completions=None, human_roles=None):
    """Return a conditional plan for one scenario.
    
    Declarations and completions are synthetic inputs. Check the human-role table
    in the same way whether it is loaded from the catalogue or supplied here."""
    validate_scenario(scenario, catalogue)
    table = load_human_roles() if human_roles is None else human_roles
    validate_human_roles(table)                      # Validate both loaded and directly supplied role tables.
    validate_roles(scenario, catalogue, table)
    declarations = declarations or {}
    validate_declarations(scenario, declarations)
    completions = set(map(tuple, completions or ()))
    validate_completions(scenario, completions)
    team = team_catalogue(scenario, catalogue)
    outside = sorted({c['platform'] for c in catalogue} - set(scenario['team']['robots']))
    live0, unused = _live_facts(catalogue, scenario)
    tasks = {t['id']: t for t in scenario['tasks']}
    reserved, occupied, rows, by_id, calls, scope_queries = set(), {}, [], {}, 0, 0
    slots = scenario['slots']
    order = sorted(scenario['tasks'], key=lambda t: (slots.index(t['slot']), scenario['tasks'].index(t)))
    for task in order:
        slot = task['slot']
        row = {'id': task['id'], 'label': task['label'], 'slot': slot, 'area': task['area'], 'location': task['location'],
               'state': None, 'route': None, 'selector': None, 'job_context': None, 'robot': None, 'person': None,
               'person_role': None, 'gap': None, 'outside_team': None, 'reasons': None, 'inputs': [], 'notes': [],
               'synthetic': []}
        by_id[task['id']] = row
        cond = scenario['conditions'][slot]
        shared, area, loc = cond['shared_safety'], cond['areas'][task['area']], cond['locations'][task['location']]
        local = [loc['safety']] + ([loc['contact_permission']] if involves_contact(task) else [])
        # Wait for required inputs before calling the selector.
        missing = []
        for producer, output in task.get('needs', []):
            if (producer, output) in completions:
                row['inputs'].append({'from': producer, 'output': output, 'status': 'available (synthetic completion)'})
                row['synthetic'].append('completion of %s' % producer)
                continue
            status = 'awaiting' if can_still_produce(by_id[producer], tasks[producer], output) else 'unobtainable'
            row['inputs'].append({'from': producer, 'output': output, 'status': status, 'producer_state': by_id[producer]['state']})
            missing.append(status)
        if missing:
            if 'FAIL' in (shared, area):              # Report a known failure before waiting for missing inputs.
                row['state'] = 'BLOCKED_SHARED'
            elif 'FAIL' in local:
                row['state'] = 'BLOCKED_TASK_CONDITION'
            else:
                row['state'] = 'CONDITIONAL_ON_INPUT' if all(s == 'awaiting' for s in missing) else 'BLOCKED_INPUT'
            rows.append(row); continue
        # Record the selector decision using the shared and local conditions.
        result = None
        if task.get('request') is not None:
            live = copy.deepcopy(live0)
            live['shared_safety'] = shared
            for c in live['candidates'].values():
                c['safety'] = loc['safety']
                if 'contact_authorized' in c['checks']:
                    c['checks']['contact_authorized'] = loc['contact_permission']
            for s, name in reserved:
                if s == slot:
                    live['platforms'][name]['availability'] = 'BUSY'
            result = select(task['request'], team, live); calls += 1
            row['selector'], row['job_context'] = _summary(result), job_context(scenario, task)
        # Check shared, area and location conditions before assigning a robot or person.
        # A known failure takes precedence over an unresolved condition.
        if 'FAIL' in (shared, area):
            row['state'] = 'BLOCKED_SHARED'; rows.append(row); continue
        if 'FAIL' in local:
            row['state'] = 'BLOCKED_TASK_CONDITION'; rows.append(row); continue
        if shared != 'PASS' or area != 'PASS':
            row['state'] = 'WITHHELD_SHARED'; rows.append(row); continue
        if any(v != 'PASS' for v in local):
            row['state'] = 'WITHHELD_TASK_CONDITION'; rows.append(row); continue
        # Pass the original declaration binding so the selector can check it.
        d, choice, confirmer = declarations.get(task['id']), None, None
        if result is not None and d:
            if d['job_context'] != row['job_context']:
                row['notes'].append('declaration not applied: the job context changed after it was made')
            else:
                if d.get('choice') and result['decision'] == 'ASK':
                    attempt = {'kind': 'choice', 'binding': d['choice']['binding'], 'candidate_id': d['choice']['candidate_id']}
                    r = select(task['request'], team, live, choice=attempt); calls += 1
                    if r['decision'] == 'ASK':
                        row['notes'].append('declared choice not applied: %s' % r['reasons'][-1])
                    else:
                        choice, result = attempt, r
                        row['synthetic'].append('choice of %s by %s' % (attempt['candidate_id'], d['choice'].get('by', 'owner')))
                if d.get('confirm') and result['decision'] == 'CONFIRM':
                    named, proposed, by = d['confirm']['candidate_id'], result['configuration']['id'], d['confirm'].get('by')
                    needed = ['confirmer'] + list(task.get('robot_roles', []))
                    person = _person(scenario['team'], occupied, slot, roles=needed, only=by) if by else None
                    if named != proposed:
                        row['notes'].append('declared confirmation not applied: it names %s; the rule proposes %s' % (named, proposed))
                    elif person is None:
                        row['notes'].append('declared confirmation not applied: ' + (
                            _why_not(scenario['team'], occupied, slot, needed, by) if by else 'it names no person'))
                    else:
                        attempt = {'kind': 'confirmation', 'binding': d['confirm']['binding'], 'candidate_id': named}
                        r = select(task['request'], team, live, choice=choice, confirmation=attempt); calls += 1
                        if r['decision'] == 'SELECT':
                            result, confirmer = r, person
                            row['synthetic'].append('confirmation of %s by %s' % (named, person))
                        else:
                            row['notes'].append('declared confirmation not applied: %s' % r['reasons'][-1])
            row['selector'] = _summary(result)
        # 5. robot option
        if result is not None and result['decision'] in ('SELECT', 'CONFIRM'):
            cfg = result['configuration']
            row['route'] = 'robot'
            roles = sorted(set(task.get('robot_roles', [])) | ({'confirmer'} if result['decision'] == 'CONFIRM' or confirmer else set()))
            person = confirmer or (_person(scenario['team'], occupied, slot, roles=roles) if roles else None)
            if roles and person is None:
                row['state'] = 'WAITING_FOR_PERSON'; row['notes'].append('no free person holds %s' % roles)
                rows.append(row); continue
            reserved.add((slot, cfg['platform']))
            row['robot'] = {'platform': cfg['platform'], 'configuration': cfg['id'], 'mode': cfg['mode'],
                            'setpoint_n': cfg['force_setpoint_n'], 'evidence_level': cfg['evidence_level']}
            if person:
                occupied[(slot, person)] = task['id']; row['person'], row['person_role'] = person, roles
            if result['decision'] == 'SELECT':
                row['state'] = 'RECOMMENDED'
                row['notes'].append('offline recommendation; no dispatch' +
                                    (' (after a synthetic confirmation)' if confirmer else ''))
            else:
                row['state'] = 'PENDING_CONFIRMATION'
                row['notes'].append('tentative reservation; the reserved person has not confirmed')
            rows.append(row); continue
        if result is not None and result['decision'] == 'ASK':
            row['state'], row['route'] = 'PENDING_CHOICE', 'robot'; row['notes'].append('owner choice among %s' % result['remaining'])
            rows.append(row); continue
        if result is not None and result['decision'] == 'WAIT':
            row['state'], row['route'] = 'WAITING', 'robot'; row['notes'].append('platform busy in this slot')
            rows.append(row); continue
        # 6. WITHHOLD/REFUSE after the conditions of step 3 passed: every in-scope reason is robot-specific
        if result is not None:
            reasons = sorted({r for c in result['candidate_reasons'].values() for r in c['failed'] + c['unresolved']})
            row['reasons'] = reasons
            assert not any(r in TASK_LEVEL for r in reasons), (task['id'], reasons)
            if not result['candidate_reasons']:          
                scope = result
                if outside:
                    scope = select(task['request'], catalogue, live_for(catalogue, 'PASS')); scope_queries += 1
                row['gap'] = evidence_gap(task['request'], scope, catalogue)
                elsewhere = [{'candidate': c['id'], 'platform': c['platform']} for c in catalogue
                             if c['platform'] in outside and c['id'] not in scope['not_applicable']]
                if elsewhere:
                    row['outside_team'] = elsewhere
                    row['notes'].append('supporting records only for robots outside the declared team (%s); not offered'
                                        % ', '.join(sorted({e['platform'] for e in elsewhere})))
        alt = task.get('manual_alternative')
        if alt is None:
            if result is None or not result['candidate_reasons']:
                row['state'] = 'BLOCKED_NOT_IN_TEAM' if row['outside_team'] else 'BLOCKED_EVIDENCE'
            else:
                row['state'] = 'BLOCKED_ROBOT_CHECK' if result['decision'] == 'REFUSE' else 'WITHHELD_ROBOT_CHECK'
            rows.append(row); continue
        row['route'] = 'manual'
        person = _person(scenario['team'], occupied, slot, training=alt['training'])
        if person is None:
            row['state'] = 'WAITING_FOR_PERSON'; row['notes'].append('no free person with training %s' % alt['training'])
            rows.append(row); continue
        occupied[(slot, person)] = task['id']
        row['state'], row['person'], row['person_role'] = 'MANUAL_ASSIGNED', person, ['manual:' + alt['training']]
        row['manual_alternative'] = {'mode': alt['mode'], 'criteria': alt['criteria'], 'produces': alt.get('produces', []),
                                     'original_request_fulfilled': task.get('request') is None}
        if task.get('request') is not None:
            row['notes'].append('manual alternative; original %s request not fulfilled' % task['request']['capability'])
        rows.append(row)
    assert all(r['state'] in STATES for r in rows)
    assert all(name in scenario['team']['robots'] for _, name in reserved)
    actions = []
    for r in rows:
        if r['state'] == 'PENDING_CONFIRMATION':
            actions.append({'task': r['id'], 'action': 'confirmation pending (confirmer reserved)', 'by': r['person']})
        if r['state'] == 'PENDING_CHOICE':
            actions.append({'task': r['id'], 'action': 'owner choice pending', 'by': 'owner'})
        if r['robot'] and r['person_role'] and 'operator' in r['person_role']:
            actions.append({'task': r['id'], 'action': 'operator command', 'by': r['person']})
        if r['robot'] and r['person_role'] and 'attendant' in r['person_role']:
            actions.append({'task': r['id'], 'action': 'set-up, start and observation', 'by': r['person']})
        for s in r['synthetic']:
            if s.startswith(('confirmation of', 'choice of')):
                actions.append({'task': r['id'], 'action': s + ' (synthetic declaration)', 'by': None})
        if r['state'] == 'MANUAL_ASSIGNED':
            actions.append({'task': r['id'], 'action': 'manual task', 'by': r['person']})
    return {'scenario': scenario['id'], 'declared_tally': scenario.get('tally', {}),
            'tally_note': 'declared work requirement; not converted to time, cost or fleet size',
            'team': {'robots': sorted(scenario['team']['robots']), 'catalogue_platforms_outside_team': outside},
            'unused_live_facts': unused,
            'tasks': rows, 'human_actions': actions, 'selector_calls': calls, 'scope_queries': scope_queries,
            'reservations': {'robots': sorted('%s:%s' % x for x in reserved),
                             'people': {'%s:%s' % k: v for k, v in sorted(occupied.items())}},
            'synthetic_inputs': {'declarations': declarations, 'completions': sorted(map(list, completions))},
            'scope': 'Offline consistency example: authored inputs, synthetic live facts, logical slots; no dispatch.'}
