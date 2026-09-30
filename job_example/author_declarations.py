"""Check the illustrative choices and confirmations in the worked example.

Each declaration refers to its task's plan and situation before confirmation.
The planner does not create approvals. These synthetic inputs do not
authenticate a person or provide a one-use permission. For tasks sharing a slot,
declarations must account for reservations already made in that slot.

Run without arguments to check the saved declarations. --write replaces them
with declarations built from the current scenario."""
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from job_plan import plan, load_catalogue, declaration_from_plan      # noqa: E402

SCENARIO = HERE / 'scenarios/tested_contexts.json'
SOURCE = 'pre-declaration plan of this scenario (results/PLAN_tested_contexts.json), task %s: selector binding and job context'
INTENTS = {
    'T-WS-D': {'choice': ('G1_D_8N', 'owner'), 'confirm': ('G1_D_8N', 'H1'),
               'basis': 'Illustrative owner choice and a synthetic confirmation by H1, carrying the binding and job context of '
                        'the pre-declaration plan; applied only in the declarations pass'},
    'T-SH': {'confirm': ('UR3_registered_handover', 'H1'),
             'basis': 'Synthetic confirmation by the operator H1, carrying the binding and job context of the '
                      'pre-declaration plan; applied only in the declarations pass'},
}


def author(scenario, catalogue):
    rows = {r['id']: r for r in plan(scenario, catalogue)['tasks']}
    return {tid: declaration_from_plan(rows[tid], snapshot_id=scenario['live']['snapshot_id'], choice=i.get('choice'),
                                       confirm=i.get('confirm'), basis=i['basis'], authored_from=SOURCE % tid)
            for tid, i in INTENTS.items()}


def main():
    scenario = json.loads(SCENARIO.read_text())
    fresh = json.loads(json.dumps(author(scenario, load_catalogue())))
    if '--write' in sys.argv[1:]:
        scenario['synthetic_declarations'] = fresh
        SCENARIO.write_text(json.dumps(scenario, indent=2) + '\n')
        print('wrote %d declarations to %s' % (len(fresh), SCENARIO.name))
        return
    same = scenario.get('synthetic_declarations') == fresh
    print(json.dumps({t: {k: d[k] for k in ('job_context',)} | {kind: d[kind] for kind in ('choice', 'confirm') if kind in d}
                      for t, d in fresh.items()}, indent=2))
    print('declarations in %s equal a fresh authoring from the pre-declaration plan: %s' % (SCENARIO.name, same))
    sys.exit(0 if same else 1)


if __name__ == '__main__':
    main()
