# Job planning specification

## Inputs (authored, declared, synthetic)

- **Capability catalogue:** the ten profiles in `catalogue_r4/CANDIDATE_MAPPING_R4.json`. These describe the evidence for each capability. The team list separately states which robots are available to the company.
- **Human roles of each mode:** `human_roles_by_mode`, declared with the catalogue. For every mode recorded in the catalogue it gives the human roles the mode needs, whether the mode includes a per-target confirmation, and the basis. For the UR3 and the UR5 these are the roles with which the mode was demonstrated. In the G1 simulation a scripted virtual hand took the person's part, so its roles are the authors' declaration of what a deployment would need. Three roles are kept apart:
  - `operator`: gives commands or indicates the target by gesture during the task;
  - `confirmer`: confirms the option the rule proposes (per-target confirmation);
  - `attendant`: starts the program and observes, giving no movement command and no per-target confirmation.

  The absence of per-target confirmation therefore never means that no person is involved. A malformed table raises `ValueError`, and so does an empty role list. No mode in this register was shown without a person, so "nobody" cannot be entered silently.
- **Team:**
  - **`team.robots`:** the declared robot platforms. It must be a list of distinct, non-empty platform names, and each must occur in the catalogue. A malformed list, a duplicate, a non-string entry or an unknown platform raises `ValueError`. The team's profiles (the *team catalogue*) are the catalogue entries whose platform is in the team. An empty team is valid and gives an empty team catalogue.
  - **`team.people`:** people, each with declared manual training, declared roles (`confirmer`, `operator`, `attendant`) and per-slot availability.
- **Job:** a list of sub-tasks with distinct IDs. Each sub-task has:
  - an ID, a label, a logical slot and an area;
  - a `location`, which names the place where the work happens;
  - either a robot request in the selector's request schema, or no request (a manual-only task, which must declare `contact`: true or false and declares its outputs only under `manual_alternative.produces`).
  - A robot request must allow exactly one target. A request allowing several targets raises `ValueError`, so the job declares one sub-task per target and location. That target must resolve, through the place register, to the task's `location` (below). Its `candidate_priority` may name only team profiles; otherwise it raises `ValueError`.

  Optional fields:
  - `needs`: inputs produced by other sub-tasks, as (task ID, output name);
  - `produces`: the outputs of the robot route;
  - `manual_alternative`: the owner-permitted mode, criteria, required training and its own `produces`, declared before evaluation. It must declare `contact`, true or false, and a task-level `contact` must be true or false. It has no other keys; a manual alternative works at its task's location;
  - `robot_roles`: the human roles a robot option needs (e.g. `operator` for gesture handover, `attendant` for the UR5 detection). It must include every role that `human_roles_by_mode` declares for the mode of any team profile the request could use, that is, any team profile of the requested capability whose mode the request allows. Before any plan is made, each of the following raises `ValueError`:
    - a sub-task that omits such a role;
    - a mode for which no human role is declared (an unknown role is never treated as no role);
    - a declared per-target confirmation that contradicts the profile's `confirmation_required`;
    - `robot_roles` that is not a list of role names;
    - a malformed human-role table, whether it is loaded with the catalogue or passed to `plan()` as `human_roles`. Mode names must be non-empty text, and each mode must have exactly a non-empty list of role names, a true/false per-target confirmation and a non-empty basis. The table is not repaired or completed: an empty role list is refused, and no mode is ever treated as needing nobody.

    This checks declared inputs against the register. It does not show that a person is competent or that a mode is safe.

  Each whiteboard, wall spot and glass partition has its own task ID. Rows with identical outcomes may be aggregated in a table only by listing every ID.
- **Place register:** `places`, keyed by place ID. The IDs are the names used in the task `location` fields and the slot conditions. Each place declares the one `area` it belongs to, and its target aliases; it may also carry a `description`. No other key is allowed.
  - **Aliases.** Each alias is a triple (`domain`, `context`, `target`) of non-empty strings. All aliases of one place share one domain. The domain and context scope the alias, so the same bare target name may belong to different places in different domains or contexts, never to two places in the same one.
  - **Several aliases per place.** One place may carry several aliases, for example the marked region and the located region of one whiteboard. A place used only by manual tasks may list none.
  - **Aliases link names.** Short and long names, such as `D` and `g1_wall_location_D`, are linked by the alias, not by string equality.
  - **Input errors.** Before any plan is made, each of the following raises `ValueError`, so no plan exists and no robot or person is assigned:
    - a malformed register or alias;
    - an alias declared for two places (ambiguous);
    - a task location, or a slot-condition location, that is not a declared place;
    - a task whose `area` is not its place's area, or an area without a declared condition in the task's slot;
    - slot conditions declared for anything other than exactly the declared slots;
    - a robot request whose domain, context or allowed targets are not text;
    - a robot request whose (domain, context, target) matches no alias (missing);
    - a robot request whose alias names a place other than the task's `location` (contradictory).
  - **What this checks.** It checks the consistency of authored inputs: a request, its task's area and location, and the conditions used for it must describe the same place. It does not check that a produced output (for example a located region) is used at the place where it was produced: `needs` and `produces` link outputs by name only. A request's `candidate_priority` may name profiles outside the request's scope; the rule never offers them. The check does not verify that a place is safe in the world, and it authenticates no one.
- **Conditions per logical slot** (declared scenario inputs, not observations; the states are `PASS`, `FAIL`, `UNKNOWN` or `CONFLICTING`):
  - a shared work-area safety state, which applies to robots and people alike;
  - an access authorisation per area;
  - per location, `safety` and `contact_permission`. Every task's location must be declared in the task's slot. These are facts about the place, independent of any robot profile.
- **Live snapshot for the selector:** synthetic, declared per scenario. It covers only the team's platforms and profiles, and holds only robot-specific facts:
  - platform availability and safety;
  - per-candidate `valid_input`, `visible_target` and `path`.

  For each call, the layer sets:
  - its shared-safety field to the slot's shared state;
  - every team candidate's local `safety` to the task location's `safety`, and its `contact_authorized` check (where the capability has one) to the location's `contact_permission`.

  A per-candidate override of `safety` or `contact_authorized` raises `ValueError`, because those are location conditions. A live fact naming a platform or candidate outside the catalogue raises `ValueError`. A fact about a catalogue platform or candidate outside the team is not used, and the plan lists it under `unused_live_facts`.
- **Declarations:** optional, and keyed by task ID; each ID must be a task with a robot request. A declaration can carry an owner choice and a confirmation:
  - **choice:** `candidate_id`, `by` and `binding`;
  - **confirm:** `candidate_id`, `by` (a named person) and `binding`.

  The `binding` is the selector's digest recorded for that task in the **pre-declaration plan**. A declaration also carries that row's `job_context` digest, may carry `snapshot_id` as a readable label, and records its `basis` and `authored_from`. A declaration without a well-formed 64-hex binding or job context, or for an unknown task, raises `ValueError`. How declarations are authored and applied is set out under **Declarations** below.
- **Synthetic completions:** optional. A completion marks an output as available. It is labelled "synthetic", and the pre-completion state is kept. A completion must name an output that its task declares; otherwise it raises `ValueError`.

## Semantics

**Logical slots.** A slot is an ordered label with no duration. Within one slot:
- a robot platform can hold at most one reservation;
- a person can hold at most one occupancy (a manual task, or the roles of one robot option: confirmer, operator or attendant).

Tasks are processed in slot order, then in the job's declared task order. Slots show only consistency with the stated resource constraints. They do not show that a floor can be cleaned in a shift.

**Contact.** A task involves contact if any of these holds:
- its robot capability has a `contact_authorized` check (regulated wiping and gesture handover);
- the task declares `contact: true`;
- its manual alternative declares `contact: true`.

**Checks run for every task, in this order:**

1. **Inputs.** If a needed output is not available:
   - a known failure is reported first: shared or area `FAIL` gives `BLOCKED_SHARED`, and a location `FAIL` that applies gives `BLOCKED_TASK_CONDITION`;
   - otherwise the state is `CONDITIONAL_ON_INPUT` if its producer can still supply it (see **Inputs and outputs**);
   - otherwise it is `BLOCKED_INPUT`.

   An unresolved condition waits until the inputs exist. The selector is not called for a task without its inputs.
2. **Selector record.** For a robot request, the unchanged selector is called with the request, the **team catalogue** and a copy of the live snapshot. That copy carries the slot's shared state and the task location's conditions (as above), and every team platform already reserved in this slot is set to `BUSY`. The result and its binding are recorded, together with the task's `job_context` digest.
3. **Shared, area and task-location conditions**. These apply to robots and people alike. They are checked before any declaration is applied, any robot is reserved or any person is assigned, and a FAIL anywhere outranks an unresolved state elsewhere:
   1. shared safety or area authorisation `FAIL`: `BLOCKED_SHARED`;
   2. location `safety` `FAIL`, or `contact_permission` `FAIL` for a task that involves contact: `BLOCKED_TASK_CONDITION`;
   3. shared safety or area authorisation `UNKNOWN` or `CONFLICTING`: `WITHHELD_SHARED`;
   4. location `safety` `UNKNOWN` or `CONFLICTING`, or `contact_permission` `UNKNOWN` or `CONFLICTING` for a task that involves contact: `WITHHELD_TASK_CONDITION`.

   None of these depends on which robot profile, force or route is in scope. A manual alternative, a different setpoint or a profile outside the request cannot turn them into PASS. Contact permission does not gate a task that involves no contact, such as detection.
4. **Declarations**. Applied only now; see **Declarations**.
5. **Robot option.** Map the selector result:

| Selector result | Plan state | Reservation |
|---|---|---|
| SELECT | `RECOMMENDED` (an offline recommendation; no dispatch) | robot reserved; roles in `robot_roles` reserved |
| CONFIRM | `PENDING_CONFIRMATION` | robot reserved tentatively, together with one free person who holds every required role (`confirmer`, plus any `robot_roles`). Reserving an available confirmer does not confirm the option |
| ASK | `PENDING_CHOICE` | nothing reserved; the remaining team options are listed |
| WAIT | `WAITING` | nothing reserved |
| WITHHOLD, REFUSE | step 6 | – |

   If a SELECT or CONFIRM option needs human roles (`robot_roles`, plus `confirmer` for CONFIRM) and no single free person in the slot holds all of them, the state is `WAITING_FOR_PERSON` on the **robot route**, and nothing is reserved.
6. **Robot-specific WITHHOLD or REFUSE.** Once step 3 has passed, every in-scope reason is robot-specific:
   - evidence checks and metrics;
   - valid input, visible target and path;
   - platform safety and availability.

   The layer asserts that no local-safety or contact-authorisation reason remains. What happens next depends on the team:
   - **No team profile in scope, but a catalogue profile of a robot outside the team is**. The task is `BLOCKED_NOT_IN_TEAM`:
     - the out-of-team profiles are listed as a gap, never as options;
     - a single additional selector call over the full catalogue, with a synthetic all-PASS snapshot, finds them; only its `not_applicable` field is used and its decision is discarded;
     - this call is made only when some catalogue platform is outside the team.
   - **Nothing in scope anywhere:** `BLOCKED_EVIDENCE`, with the evidence gap computed against the full catalogue.
7. **Manual alternative**. This step runs only for a manual-only task, or after `BLOCKED_NOT_IN_TEAM`, `BLOCKED_EVIDENCE` or a robot-specific WITHHOLD or REFUSE. A person is assigned (`MANUAL_ASSIGNED`, **manual route**) only if all of the following hold:
   - the task declares a `manual_alternative`;
   - a roster person has that declared training;
   - that person is available in the slot and not yet occupied;
   - the task's inputs exist.

   The original robot request stays unmet. The plan records "manual alternative; original request not fulfilled". A regulated-force request is never marked fulfilled by an unregulated manual alternative.

   If a manual alternative is declared but no person qualifies, the state is `WAITING_FOR_PERSON` on the manual route. If no manual alternative is declared, the state is:
   - `BLOCKED_NOT_IN_TEAM` or `BLOCKED_EVIDENCE`, as in step 6, with the gap;
   - `BLOCKED_ROBOT_CHECK`, when in-scope team options fail robot-specific checks (REFUSE);
   - `WITHHELD_ROBOT_CHECK`, when they are unresolved (WITHHOLD); the reasons are listed.

   The gap shows each same-capability record's value against the requested value.

**Declarations.**
- **Authoring.** A declaration is authored from a recorded **pre-declaration plan**. Its `binding` is copied from that plan row's selector binding, and its `job_context` from the row's job-context digest (`declaration_from_plan`). `plan()` never creates or refreshes a declaration. The delivered declarations are authored this way from `results/PLAN_tested_contexts.json` (`author_declarations.py`). A test checks that they still equal that plan's values, and the pre-declaration plan is kept as its own output. Any fresh approval is a new, separately labelled synthetic input.
- **Application.** A declaration is considered only after step 3 has passed:
  1. **Job context.** If the task's current `job_context` differs from the declared one, nothing is applied and the note says so. The digest covers:
     - the task as declared (request, slot, area, location, roles, needs, outputs and manual alternative);
     - the task's place record, with its target aliases;
     - the slot's shared safety;
     - the area authorisation;
     - the location conditions;
     - the declared robot team.

     So changing the layer's own constraints (moving the task to another slot, changing its roles, location conditions, place association or the team) cannot silently preserve an approval.
  2. **Choice.** A choice must name who made it (`by`); otherwise it raises `ValueError`. If the selector returned ASK, the declared choice is passed as `{kind: choice, binding: <declared binding>, candidate_id}`, with the binding unchanged. The selector decides whether that binding is current. A stale or non-remaining choice stays ASK (`PENDING_CHOICE`), and the note quotes the selector's reason.
  3. **Confirmation.** If the result is CONFIRM, the declared confirmation is passed only if two things hold: it names exactly the proposed option, and it names a person who holds `confirmer` (and any `robot_roles`), is available in the slot and is free. It is passed as `{kind: confirmation, binding: <declared binding>, candidate_id}`, with the binding unchanged. If the binding is stale, the selector returns CONFIRM, and the task stays `PENDING_CONFIRMATION` with the selector's reason. Only an actual SELECT gives `RECOMMENDED (after a synthetic confirmation)`, attributed to the named person.
- **What the selector binding covers.** It is the digest of:
  - the request;
  - the team catalogue, so a changed assessment or a changed team changes it;
  - the live snapshot: the label, shared safety, the location conditions mapped per candidate, platform states, robot-specific checks and the reservations already made in the slot.

  The binding checks the snapshot contents as well as its label. Changed contents with the same label make the declaration stale.
- **Several declarations in one slot.** The binding covers the reservations already made in the slot. The declaration for a later task in the same slot is therefore authored from a plan that already applies the earlier task's declaration, one after another. A declaration authored from the plan made before any declaration becomes stale once an earlier task's approval reserves a robot. The delivered declarations are in different slots.
- **What is not claimed.** Declarations are synthetic consistency inputs. They are not authenticated, time-limited or one-use permissions, and an unchanged context keeps accepting the same declaration, as in the selector.

**Inputs and outputs.**
- **Declared outputs.** Before planning, every `needs` entry must name an output that its producer declares, either for its robot route (`produces`) or for its manual alternative (`manual_alternative.produces`), and the producer must come earlier.
- **Routes.** Each row records its route:
  - `robot`: RECOMMENDED, PENDING_CONFIRMATION, PENDING_CHOICE, WAITING, or WAITING_FOR_PERSON for a robot option's roles;
  - `manual`: MANUAL_ASSIGNED, or WAITING_FOR_PERSON for a manual alternative's training;
  - none: every blocked or withheld state.
- **When a pending producer counts.** A producer can still supply an output only through the route it is on:
  - a robot route supplies only `produces`;
  - a manual route supplies only `manual_alternative.produces`;
  - a producer that is itself `CONDITIONAL_ON_INPUT` counts through `produces` only.

  A manual alternative that lacks the output therefore cannot supply it, and a robot route waiting for its operator still can. A producer that is itself conditional counts as a possible producer until it is evaluated, unless a known failure has already blocked it (step 1).

**Human actions required** are listed per plan:
- pending confirmations, and who is reserved to give them;
- pending owner choices;
- operator commands;
- set-up, start and observation by an attendant;
- manual tasks and who performs them.

This descriptive count does not measure workload.

## Output

For each variant, a plan (JSON) and a Markdown table are generated only from actual selector calls and the authored inputs above. Each task has:
- its state and route;
- the selector's decision, reason and binding, where called, and its job context;
- its reservation (robot, person, role);
- its evidence gap, and any out-of-team records;
- its input status;
- the synthetic markers.

The plan also carries:
- the declared team;
- the catalogue platforms outside it;
- any unused live facts;
- the number of scope queries;
- the declared quantity tally (L1 work requirement), which is never converted to time, cost or fleet size.

## Required checks (tests)


1. Every task has exactly one state.
2. No robot is reserved without a SELECT or CONFIRM for that exact request and slot, and never outside the declared team.
3. No person is assigned without the declared training or role, availability and a free slot.
4. No robot or person is double-booked in a slot (with positive and negative cases).
5. Shared safety `FAIL` and `UNKNOWN`, and area `FAIL` and `UNKNOWN`, with a trained, available person: no robot or person is assigned (deliberately failed safety checks).
6. A task needing an unavailable input is `CONDITIONAL_ON_INPUT` or `BLOCKED_INPUT`, never assigned.
7. A regulated-force request is never fulfilled by a manual alternative.
8. CONFIRM stays pending without a declaration, becomes SELECT with a declaration carrying its original binding, and returns to pending after a snapshot change.
9. ASK stays an open choice until a declared choice is given.
10. A busy platform gives `WAITING`.
11. A team without G1, and a catalogue without the G1 profiles, leave the customer-building plan unchanged, because no simulation record is used for a physical request.
12. Swapping in the earlier register changes only the rehearsal D task. The earlier register is the current catalogue without its four 8 N records, so the UR5 human-role assessment stays unchanged. The rule's outcome changes from ASK (7 N or 8 N) to CONFIRM at 7 N. In the full scenario the task then waits for a person, because H1 attends the UR5 detection in the same slot; with H1 free it is PENDING_CONFIRMATION at 7 N.
13. Regression: the selector gives the expected decisions, and the reproduction and catalogue tests pass.
14. A declaration made for another snapshot, or naming no person or an unqualified person, is not applied.
15. Location safety or contact permission that is FAIL or UNKNOWN, with a declared manual alternative and a trained, free person, assigns nobody.
16. A robot-specific withholding, for example unknown availability, is labelled as such and may use a declared manual alternative.
17. A `needs` entry naming an undeclared output raises an error, and a manual alternative that declares no output leaves its consumer blocked.


18. - Complete, partial and empty teams: out-of-team robots are never offered, chosen, confirmed or reserved.
    - `BLOCKED_NOT_IN_TEAM` names the out-of-team record, and a declared manual alternative is used for it.
    - The delivered declarations recommend nothing for an empty team.
    - Malformed, duplicate and unknown roster entries, and live facts naming unknown platforms, raise errors.
19. Declarations carry their pre-declaration bindings. For a choice and for a confirmation:
    - the unchanged declaration applies;
    - a changed request, assessment, live fact, snapshot label, team or job context with the same label leaves the task pending;
    - the direct selector, given the original binding, agrees in each case;
    - wrong candidates and malformed declarations are not applied, or are rejected.
20. Location safety and contact permission, FAIL, UNKNOWN or CONFLICTING, block or withhold:
    - with every profile out of scope (an unsupported 9 N request) and a trained person;
    - with two force profiles at one location;
    - for a manual-only task (contact permission only when it involves contact).

    With safe positive controls, an unrelated location stays unaffected, and robot-specific failures at the same location stay robot-specific. Per-candidate overrides of the location facts, and a task location missing from its slot, raise errors.
21. A robot route waiting for its operator leaves its consumer `CONDITIONAL_ON_INPUT`. A manual route whose alternative lacks the output leaves it `BLOCKED_INPUT`, and one whose alternative declares it leaves it conditional.

22. A request allowing several targets, a `candidate_priority` naming an out-of-team profile, a manual alternative without a boolean `contact`, a manual-only task with top-level outputs, a completion naming no declared output, a choice without `by`, duplicate task IDs and an invalid location value on a manual-only task all raise errors. A contact-making manual alternative, or a robot task declaring contact, is gated by contact permission. A known failure is reported before a missing input. The notes name the real reason a confirmation is not applied.

23. These cases raise errors:
    - a robot request whose target resolves to a place other than its `location` (safety and contact permission set to FAIL, UNKNOWN or CONFLICTING);
    - a target with no alias;
    - an alias declared for two places;
    - an undeclared task or condition location.

    The correctly associated controls block, withhold or proceed as before. Aliases are scoped by domain and context. The nominal UR3, UR5 and G1 aliases resolve. A changed place record leaves an earlier approval pending. A task whose area differs from its place's area is refused, for robot and manual-only tasks alike.

24. - The UR5 detection reserves a free person who holds the attendant role, and the plan lists "set-up, start and observation" as a human action. It requests no per-target confirmation.
    - Without a free attendant, the state is `WAITING_FOR_PERSON` on the robot route, the robot is not reserved, and a person without the role is never used.
    - A sub-task that omits the role of its mode, a mode with no declared human role, a confirmation requirement that contradicts the record and a malformed role table raise errors.
    - Contention: while H1 attends the UR5 detection, a declared confirmation by H1 for another task in the same slot is not applied, and it is not reassigned to another person.

Checks 11–13 hold by the selector's design. They are regression checks and do not count as findings.
