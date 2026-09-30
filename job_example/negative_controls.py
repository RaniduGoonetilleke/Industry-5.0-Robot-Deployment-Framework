"""Check that the job-plan tests detect all 56 deliberate faults.

Each fault changes one check in a temporary copy of the planner. The named test
must fail, and an import error alone does not count. The wrong-candidate probe also
checks whether the selector keeps the task pending when the planner's own name
check is removed. No supplied source file is changed.

Run from the package root: python3 -B job_example/negative_controls.py"""
import sys
sys.dont_write_bytecode = True
import json, os, shutil, subprocess, tempfile, time
from pathlib import Path
PKG = Path(__file__).resolve().parents[1]

# Name: ([(old, new), ...], [tests that must fail])
REPAIRED = {
    'R1a team filter removed (the whole catalogue reaches the selector)': (
        [("    return [c for c in catalogue if c['platform'] in robots]\n", "    return list(catalogue)\n")],
        ['test_complete_partial_and_empty_teams', 'test_r1_empty_team_recommends_nothing']),
    'R1b roster validation removed (malformed, duplicate and unknown entries accepted)': (
        [("    if type(robots) is not list or not all(type(r) is str and r.strip() for r in robots):\n"
          "        raise ValueError('team.robots must be a list of platform names')\n"
          "    if len(set(robots)) != len(robots):\n        raise ValueError('team.robots lists a platform more than once')\n",
          ""),
         ("    if unknown:\n        raise ValueError('team.robots names platforms with no capability record: %s' % unknown)\n", "")],
        ['test_malformed_or_unknown_roster_entries_are_rejected']),
    'R1c live facts naming an unknown platform accepted': (
        [("            if name not in platforms:\n                raise ValueError", "            if False:\n                raise ValueError")],
        ['test_malformed_or_unknown_roster_entries_are_rejected']),
    'R1d declared outcome for an absent robot dropped (reported as no record)': (
        [("                row['state'] = 'BLOCKED_NOT_IN_TEAM' if row['outside_team'] else 'BLOCKED_EVIDENCE'\n",
          "                row['state'] = 'BLOCKED_EVIDENCE'\n")],
        ['test_complete_partial_and_empty_teams']),
    'R2a declared choice re-bound to the current binding': (
        [("'binding': d['choice']['binding']", "'binding': result['binding']")],
        ['test_stale_contents_or_context_leave_a_choice_pending']),
    'R2b declared confirmation re-bound to the current binding': (
        [("'binding': d['confirm']['binding']", "'binding': result['binding']")],
        ['test_stale_contents_or_context_leave_a_confirmation_pending', 'test_r2_original_declaration_after_changed_contents']),
    'R2c job-context check removed': (
        [("            if d['job_context'] != row['job_context']:\n", "            if False:\n")],
        ['test_stale_contents_or_context_leave_a_confirmation_pending', 'test_stale_contents_or_context_leave_a_choice_pending']),
    'R2d declaration schema check removed (declarations without a binding accepted)': (
        [("    validate_declarations(scenario, declarations)\n", "")],
        ['test_wrong_candidates_people_and_malformed_declarations']),
    'R3a location FAIL gate removed': (
        [("        if 'FAIL' in local:\n            row['state'] = 'BLOCKED_TASK_CONDITION'; rows.append(row); continue\n", "")],
        ['test_location_fail_or_unresolved_blocks_every_route', 'test_r3_location_hazard_cannot_be_lost']),
    'R3b location UNKNOWN/CONFLICTING gate removed': (
        [("        if any(v != 'PASS' for v in local):\n            row['state'] = 'WITHHELD_TASK_CONDITION'; rows.append(row); continue\n", "")],
        ['test_location_fail_or_unresolved_blocks_every_route', 'test_manual_only_task_follows_the_location']),
    'R3c location conditions not passed to the selector': (
        [("            for c in live['candidates'].values():\n                c['safety'] = loc['safety']\n"
          "                if 'contact_authorized' in c['checks']:\n                    c['checks']['contact_authorized'] = loc['contact_permission']\n", "")],
        ['test_location_fail_or_unresolved_blocks_every_route']),
    'R3d contact permission never gated': (
        [("        local = [loc['safety']] + ([loc['contact_permission']] if involves_contact(task) else [])\n",
          "        local = [loc['safety']]\n")],
        ['test_location_fail_or_unresolved_blocks_every_route', 'test_manual_only_task_follows_the_location']),
    'R3e per-candidate location overrides applied again': (
        [("        if set(override) != {'checks'} or 'contact_authorized' in override['checks']:\n"
          "            raise ValueError('%s: local safety and contact permission are task-location conditions; declare them under '\n"
          "                             'conditions[slot].locations, not per candidate' % cid)\n", ""),
         ("            live['candidates'][cid]['checks'].update(override['checks'])\n",
          "            live['candidates'][cid]['checks'].update(override.get('checks', {}))\n")],
        ['test_location_facts_are_never_declared_per_robot_profile', 'test_r3_location_hazard_cannot_be_lost']),
    'N1 pending route not tracked': (
        [("    if row['route'] == 'robot' and row['state'] in ROBOT_ROUTE:\n",
          "    if row['state'] in ('RECOMMENDED', 'PENDING_CONFIRMATION', 'PENDING_CHOICE', 'WAITING'):\n"),
         ("    if row['route'] == 'manual' and row['state'] in ('MANUAL_ASSIGNED', 'WAITING_FOR_PERSON'):\n",
          "    if row['state'] in ('MANUAL_ASSIGNED', 'WAITING_FOR_PERSON'):\n")],
        ['test_a_robot_waiting_for_its_operator_can_still_supply_its_output', 'test_n1_waiting_operator_keeps_consumer_conditional']),
    'P1 multi-target requests accepted again (pre-seal review F1)': (
        [("            if len(t['request']['allowed_targets']) != 1:     # Each robot task has one target and its own location conditions.\n"
          "                raise ValueError('%s allows several targets; declare one sub-task per target and location' % t['id'])\n", "")],
        ['test_a_robot_sub_task_names_one_target']),
    'P2 out-of-team preference passed to the rule (F4)': (
        [("            if outside:\n                raise ValueError('%s: candidate_priority names profiles outside the declared team: %s' % (t['id'], outside))\n", "")],
        ['test_a_preference_for_a_robot_outside_the_team_is_refused_explicitly']),
    'P3 manual alternative contact ignored (F2, reviewer MX1)': (
        [("\n                or (task.get('manual_alternative') or {}).get('contact'))", ")")],
        ['test_contact_is_declared_on_every_manual_alternative']),
    'P4 contact declared on a robot task ignored (F2, reviewer MX2)': (
        [("request['capability'] in CONTACT_CAPABILITIES) or task.get('contact')",
          "request['capability'] in CONTACT_CAPABILITIES) or (request is None and task.get('contact'))")],
        ['test_contact_is_declared_on_every_manual_alternative']),
    'P5 manual alternative without a declared contact accepted (F2)': (
        [("        if t.get('manual_alternative') is not None and type(t['manual_alternative'].get('contact')) is not bool:\n"
          "            raise ValueError('%s: a manual alternative must declare contact (true or false)' % t['id'])\n", "")],
        ['test_contact_is_declared_on_every_manual_alternative']),
    'P6 known failure hidden behind a missing input (F10)': (
        [("            if 'FAIL' in (shared, area):              # Report a known failure before waiting for missing inputs.\n"
          "                row['state'] = 'BLOCKED_SHARED'\n            elif 'FAIL' in local:\n                row['state'] = 'BLOCKED_TASK_CONDITION'\n"
          "            else:\n                row['state'] = 'CONDITIONAL_ON_INPUT' if all(s == 'awaiting' for s in missing) else 'BLOCKED_INPUT'\n",
          "            row['state'] = 'CONDITIONAL_ON_INPUT' if all(s == 'awaiting' for s in missing) else 'BLOCKED_INPUT'\n")],
        ['test_a_known_failure_is_reported_before_inputs_exist', 'test_an_unrelated_location_is_unaffected']),
    'P7 duplicate task IDs accepted (F11, reviewer MX10)': (
        [("    if len(set(ids)) != len(ids):\n        raise ValueError('task IDs must be distinct')\n", "")],
        ['test_schema_gaps_found_in_review_are_closed']),
    'P8 location values not validated (F11, reviewer MX12)': (
        [("        if type(loc) is not dict or set(loc) != {'safety', 'contact_permission'} or \\\n"
          "                not all(v in CONDITION_STATES for v in loc.values()):\n",
          "        if type(loc) is not dict or set(loc) != {'safety', 'contact_permission'}:\n")],
        ['test_schema_gaps_found_in_review_are_closed']),
    'P9 completions naming no declared output accepted (F9)': (
        [("    validate_completions(scenario, completions)\n", "")],
        ['test_schema_gaps_found_in_review_are_closed']),
    'P10 choice without its maker accepted (F9)': (
        [("        if d.get('choice') is not None and not (type(d['choice'].get('by')) is str and d['choice']['by'].strip()):\n"
          "            raise ValueError('%s choice must name who made it (by)' % tid)\n", "")],
        ['test_schema_gaps_found_in_review_are_closed']),
    'L1 target/location contradiction accepted': (
        [("            if place != t['location']:\n"
          "                raise ValueError('%s: its target names place %s but its location is %s' % (t['id'], place, t['location']))\n", "")],
        ['test_a_request_and_its_conditions_must_describe_the_same_place', 'test_a_changed_association_does_not_keep_an_approval']),
    'L2 target without a declared place accepted (L)': (
        [("            if place is None:\n                raise ValueError('%s: no declared place has the alias (%s, %s, %s)' % (\n"
          "                    t['id'], t['request']['domain'], t['request']['context'], t['request']['allowed_targets'][0]))\n", "")],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L3 alias declared for two places accepted (L)': (
        [("            if index.get(key, pid) != pid:\n"
          "                raise ValueError('ambiguous alias %s: declared for places %s and %s' % (key, index[key], pid))\n", "")],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L4 place record left out of the approval context (L)': (
        [("    return digest({'task': task, 'place': scenario['places'][task['location']], 'shared_safety': cond['shared_safety'],",
          "    return digest({'task': task, 'shared_safety': cond['shared_safety'],")],
        ['test_a_changed_association_does_not_keep_an_approval']),
    'L5 alias looked up by bare target, without domain and context (L)': (
        [("            key = (alias['domain'], alias['context'], alias['target'])\n", "            key = alias['target']\n"),
         ("    return index.get((request['domain'], request['context'], request['allowed_targets'][0]))\n",
          "    return index.get(request['allowed_targets'][0])\n")],
        ['test_domain_and_context_scope_each_alias']),
    'L6 undeclared task location accepted (L)': (
        [("        if t['location'] not in scenario['places']:\n"
          "            raise ValueError('%s: location %s is not a declared place' % (t['id'], t['location']))\n", "")],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L7 conditions for an undeclared place accepted (L)': (
        [("            if loc not in scenario['places']:\n", "            if False:\n")],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L8 task area not tied to its place (pre-seal review F1)': (
        [("        if scenario['places'][t['location']]['area'] != t['area']:        # Use the authorisation for this place's own area.\n", '        if False:\n')],
        ['test_a_task_area_must_be_its_place_area']),
    'L9 contradiction ignored when a manual alternative is declared (review F2)': (
        [("            if place != t['location']:\n", "            if place != t['location'] and t.get('manual_alternative') is None:\n")],
        ['test_a_request_and_its_conditions_must_describe_the_same_place']),
    'L10 alias scoped by context only, domain dropped (review F2)': (
        [("            key = (alias['domain'], alias['context'], alias['target'])\n", "            key = (alias['context'], alias['target'])\n"), ("    return index.get((request['domain'], request['context'], request['allowed_targets'][0]))\n", "    return index.get((request['context'], request['allowed_targets'][0]))\n")],
        ['test_domain_and_context_scope_each_alias']),
    'L11 blank alias values accepted (review F2)': (
        [('                    not all(type(v) is str and v.strip() for v in alias.values()):\n', '                    not all(type(v) is str for v in alias.values()):\n')],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L12 condition places checked in the first slot only (review F2)': (
        [("    for slot in slots:\n        for loc in scenario['conditions'][slot].get('locations', {}):\n", "    for slot in slots[:1]:\n        for loc in scenario['conditions'][slot].get('locations', {}):\n")],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L13 unknown place-record keys accepted (review F2)': (
        [("                or not {'area', 'targets'} <= set(place) <= {'area', 'targets', 'description'} \\\n", "                or not {'area', 'targets'} <= set(place) \\\n")],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L14 request field types unchecked (review F5)': (
        [("                raise ValueError('%s: the request\\'s domain, context and allowed targets must be text' % t['id'])\n", '                pass\n')],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L15 manual alternative with unknown keys accepted (review F6)': (
        [("            raise ValueError('%s: a manual alternative has unknown keys; it works at its task\\'s location' % t['id'])\n", '            pass\n')],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L16 place mixing domains accepted (review F7)': (
        [("            raise ValueError('place %s: its aliases mix domains' % pid)\n", '            pass\n')],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
    'L17 conditions for undeclared slots accepted (review F8)': (
        [("        raise ValueError('conditions must be declared for exactly the slots %s' % slots)\n", '        pass\n')],
        ['test_missing_ambiguous_or_undeclared_associations_fail_closed']),
}
HUMAN_ROLES = {   # Human-role checks.
    'H1 a sub-task that omits the human role of its mode is accepted': (
        [("            if undeclared:\n", "            if False:\n")],
        ['test_a_sub_task_must_declare_the_roles_of_its_mode']),
    'H2 a mode with no declared human role is treated as needing nobody': (
        [("            if entry is None:\n                raise ValueError", "            if entry is None:\n                continue\n            if False:\n                raise ValueError")],
        ['test_an_unknown_human_role_is_never_treated_as_none']),
    'H3 a confirmation requirement that contradicts the record is accepted': (
        [("            if entry['per_target_confirmation'] is not c['confirmation_required']:\n", "            if False:\n")],
        ['test_the_confirmation_requirement_must_agree_with_the_record']),
    'H4 the attendant\'s set-up, start and observation is not listed as a human action': (
        [("        if r['robot'] and r['person_role'] and 'attendant' in r['person_role']:\n"
          "            actions.append({'task': r['id'], 'action': 'set-up, start and observation', 'by': r['person']})\n", "")],
        ['test_the_ur5_detection_reserves_its_attendant_and_lists_the_action']),
    'H5 the human-role check is not called': (
        [("    validate_roles(scenario, catalogue, table)\n", "")],
        ['test_a_sub_task_must_declare_the_roles_of_its_mode', 'test_an_unknown_human_role_is_never_treated_as_none',
         'test_the_confirmation_requirement_must_agree_with_the_record']),
    'H6 a malformed role table is accepted': (
        [("            raise ValueError('human_roles_by_mode[%s] needs roles, per_target_confirmation and a basis' % (mode,))\n",
          "            pass\n")],
        ['test_malformed_role_tables_are_rejected']),
    'H7 a role table passed to plan() skips the structural check': (
        [("    validate_human_roles(table)                      # Validate both loaded and directly supplied role tables.\n", "")],
        ['test_a_role_table_passed_to_plan_is_checked_like_a_loaded_one', 'test_r31_role_table_probe_through_plan']),
}
CARRIED = {   # Additional assignment and declaration faults.
    'M1 shared and area conditions ignored': (
        [("        if 'FAIL' in (shared, area):\n            row['state'] = 'BLOCKED_SHARED'; rows.append(row); continue\n", ""),
         ("        if shared != 'PASS' or area != 'PASS':\n            row['state'] = 'WITHHELD_SHARED'; rows.append(row); continue\n", "")],
        ['test_shared_or_area_fail_or_unknown_never_assigns_a_trained_available_person_or_robot']),
    'M2 dependency check removed (C2)': ([("        if missing:\n", "        if False:\n")],
                                         ['test_tested_context_outcomes_before_and_after_synthetic_declarations',
                                          'test_customer_building_outcomes']),
    'M3 CONFIRM treated as a recommendation (C3)': ([("            if result['decision'] == 'SELECT':\n",
                                                      "            if result['decision'] in ('SELECT','CONFIRM'):\n")],
                                                    ['test_confirm_stays_pending_select_with_bound_declaration_and_pending_again_after_snapshot_change']),
    'M4 person occupancy ignored (double-booking)': (
        [("        if p['availability'].get(slot) != 'AVAILABLE' or (slot, p['id']) in occupied:\n",
          "        if p['availability'].get(slot) != 'AVAILABLE':\n")], ['test_person_double_booking_gives_waiting_for_person']),
    'M5 robot reservations not marked busy (double-booking)': (
        [("                    live['platforms'][name]['availability'] = 'BUSY'\n", "                    pass\n")],
        ['test_robot_double_booking_gives_wait']),
    'M6 manual alternative marked as fulfilling a regulated request (C2)': (
        [("'original_request_fulfilled': task.get('request') is None}", "'original_request_fulfilled': True}")],
        ['test_generated_variants_satisfy_the_invariants']),
    'M10 declared outputs not checked (C2)': ([("    validate_scenario(scenario, catalogue)\n", "")],
                                              ['test_declared_outputs_are_checked']),
}


# The selector checks which candidate a confirmation names.
# Removing the planner's duplicate name check should keep the task pending.
# Check both the explanatory note and the resulting plan state.

STATE_PROBES = {
    'M8 declared confirmation applied to any proposed option (C3)': (
        [("                    if named != proposed:\n", "                    if False:\n")],
        # Request 8 N at D with a current binding but a confirmation naming the 7 N option.
        "import json,sys,copy; sys.path.insert(0,'job_example'); from job_plan import plan,load_catalogue,declaration_from_plan; "
        "C=load_catalogue(); S=json.load(open('job_example/scenarios/tested_contexts.json')); S.pop('synthetic_declarations'); "
        "s=copy.deepcopy(S); s['tasks']=[t for t in s['tasks'] if t['id']=='T-WS-D']; "
        "s['tasks'][0]['request']['force'].update(minimum_n=8.0,maximum_n=8.0); "
        "pre={r['id']:r for r in plan(s,C)['tasks']}['T-WS-D']; "
        "d={'T-WS-D':declaration_from_plan(pre,snapshot_id=s['live']['snapshot_id'],confirm=('G1_D_7N','H1'),basis='probe',authored_from='probe')}; "
        "r={x['id']:x for x in plan(s,C,declarations=d)['tasks']}['T-WS-D']; "
        "print(json.dumps({'state':r['state'],'synthetic':r['synthetic'],'notes':r['notes'],"
        "'binding_current':d['T-WS-D']['confirm']['binding']==pre['selector']['binding']}))",
        ['test_confirmation_notes_name_the_actual_reason']),
}


def run(pkg, env):
    p = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'job_example/test_job_plan.py'], cwd=pkg,
                       capture_output=True, text=True, env=env)
    failing = sorted({l.split(' ')[1] for l in p.stderr.splitlines() if l.startswith(('FAIL:', 'ERROR:'))})
    return p.returncode, failing, p.stderr


def main():
    results, t0 = {}, time.time()
    with tempfile.TemporaryDirectory(prefix='job-negative-controls-') as tmp:
        pkg = Path(tmp) / 'pkg'
        shutil.copytree(PKG / 'job_example', pkg / 'job_example', ignore=shutil.ignore_patterns('results', '__pycache__'))
        for name in ('selector', 'catalogue_r3', 'catalogue_r4', 'analysis'):
            (pkg / name).symlink_to(PKG / name)
        target = pkg / 'job_example/job_plan.py'
        original = target.read_text()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        code, failing, err = run(pkg, env)
        assert code == 0 and not failing, err[-3000:]
        for group, mutants in (('repaired', REPAIRED), ('human_roles', HUMAN_ROLES), ('additional_checks', CARRIED)):
            for name, (subs, expected) in mutants.items():
                text = original
                for a, b in subs:
                    assert text.count(a) == 1, (name, a[:70])
                    text = text.replace(a, b)
                target.write_text(text)
                code, failing, _ = run(pkg, env)
                results[name] = {'group': group, 'caught': code != 0 and bool(failing),   # An import crash with no named test is not a catch
                 'failing_tests': failing, 'targeted_tests': expected,
                                 'targeted_tests_fail': all(e in failing for e in expected)}
                target.write_text(original)
        for name, (subs, probe, expected) in STATE_PROBES.items():
            text = original
            for a, b in subs:
                assert text.count(a) == 1, (name, a[:70])
                text = text.replace(a, b)
            target.write_text(text)
            code, failing, _ = run(pkg, env)
            p = subprocess.run([sys.executable, '-B', '-c', probe], cwd=pkg, capture_output=True, text=True, env=env)
            seen = json.loads(p.stdout)
            same = (seen['binding_current'] and seen['state'] == 'PENDING_CONFIRMATION' and seen['synthetic'] == []
                    and any('confirm this exact task/configuration' in n for n in seen['notes']))
            results[name] = {'group': 'confirmation_state_probe', 'caught': code != 0 and bool(failing),   # An import crash with no named test is not a catch
                 'failing_tests': failing,
                             'targeted_tests': expected, 'targeted_tests_fail': all(e in failing for e in expected),
                             'probe_under_mutant': seen, 'state_unchanged_because_selector_refuses_the_mismatch': same}
            target.write_text(original)
        assert target.read_text() == original
    out = {'package': 'job_example', 'unmutated_suite_passes': True, 'seconds': round(time.time() - t0, 1), 'mutants': results,
           'all_caught': all(v['caught'] for v in results.values()),
           'all_targeted_tests_fail': all(v['targeted_tests_fail'] for v in results.values()),
           'm8_state_probe_confirms_selector_check': all(v['state_unchanged_because_selector_refuses_the_mismatch']
                                                         for v in results.values() if 'probe_under_mutant' in v)}
    for name, v in results.items():
        print('%-5s %-5s %2d failing  %s' % ('OK' if v['caught'] else 'MISS', 'T-OK' if v['targeted_tests_fail'] else 'T-MISS',
                                              len(v['failing_tests']), name))
    print('M8 state probe (current binding, wrong candidate): state unchanged under the mutant: %s' % out['m8_state_probe_confirms_selector_check'])
    print('all caught: %s; every targeted test fails: %s; %.1f s' % (out['all_caught'], out['all_targeted_tests_fail'], out['seconds']))
    assert out['all_caught'] and out['all_targeted_tests_fail'] and out['m8_state_probe_confirms_selector_check']


if __name__ == '__main__':
    main()
