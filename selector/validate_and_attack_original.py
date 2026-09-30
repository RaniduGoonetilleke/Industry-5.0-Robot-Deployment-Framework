"""Test whether the selector tests detect 82 single-change faults.

Each fault is applied in a temporary copy containing the required catalogue
inputs. A mutation is caught only if all baseline tests run and at least one
assertion fails. Converted exceptions and side-effect errors are reported
separately. Example outputs must also match across four Python hash seeds.
Logs are saved under verification/selector_mutations/.

Run from the package root:
python3 -B selector/validate_and_attack_original.py
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / 'verification' / 'selector_mutations'
FILES = ['selector.py', 'test_selector.py', 'build_examples.py']
MUTATIONS = [
    # Core decision and input checks.
    ('ignore_shared_hazard', 'if live["shared_safety"] == "FAIL":', 'if False:'),
    ('ignore_exact_force', 'if f is not None and c["capability"] == "regulated_wipe" and not f["minimum_n"] <= c["force_setpoint_n"] <= f["maximum_n"]:', 'if False:'),
    ('accept_unknown_evidence', 'if v in {"UNKNOWN", "CONFLICTING"}', 'if False'),
    ('ignore_mode_requirement', 'if c["mode"] not in request["allowed_modes"]:', 'if False:'),
    ('ignore_domain_mismatch', 'for field in ("capability", "domain", "context")', 'for field in ("capability", "context")'),
    ('ignore_metric_bound', 'if (value < bound) if requirement["comparison"] == "value_at_least" else (value > bound):', 'if False:'),
    ('compare_metric_quantity_only', '_definition(m) == definition', 'm["quantity"] == definition["quantity"]'),
    ('accept_stale_choice', 'if choice["binding"] != binding:', 'if False:'),
    ('skip_confirmation_binding',
     'if confirmation != {"kind": "confirmation", "binding": binding, "candidate_id": selected["id"]}:',
     'if confirmation is None:'),
    ('empty_applicable_set_means_impossible',
     'return finish("WITHHOLD", "no record for the requested capability, domain, context, target, mode, settings, setpoint "',
     'return finish("REFUSE", "no record for the requested capability, domain, context, target, mode, settings, setpoint "'),
    ('coerce_setting_types', 'if type(a) is not type(b):\n        return False', 'if False:\n        return False'),
    ('ignore_busy_platform', 'elif p["availability"] == "BUSY":', 'elif False:'),
    ('arbitrary_tie_break', 'if len(ready) != 1:', 'if False:'),
    ('drop_preference_justification', '_require(bool(basis.strip()), "preference needs basis")', 'pass'),
    # Scope, validation and evidence checks.
    ('R1_target_mismatch_stays_applicable', 'if mismatch:', 'if mismatch and "target" not in mismatch:'),
    ('R2_ignore_platform_safety', 'platform_safety=p["safety"]', 'platform_safety="PASS"'),
    ('R2_unknown_availability_as_available', 'if p["availability"] == "UNKNOWN":', 'if False:'),
    ('R3_ignore_magnitude_comparison', 'value = abs(value)', 'pass'),
    ('R4_shallow_nested_equality', 'return set(a) == set(b) and all(same(a[k], b[k]) for k in a)', 'return a == b'),
    ('R4_signed_zero_equal', 'if type(a) is float and math.copysign(1.0, a) != math.copysign(1.0, b):', 'if False:'),
    ('R5_unhashable_type_error', '_member(live["shared_safety"], STATES)', 'live["shared_safety"] in STATES'),
    ('R5_non_json_type_error', 'except (TypeError, ValueError, OverflowError, RecursionError) as error:', 'except (ValueError, RecursionError) as error:'),
    ('R5_huge_integer_overflow', 'except OverflowError:\n        return False', 'except ZeroDivisionError:\n        return False'),
    ('R6_ignore_evidence_level', 'if c["evidence_level"] not in request["accepted_evidence_levels"]:', 'if False:'),
    ('R7_shared_gate_before_evaluation', 'for c in applicable:\n        failures, unknown = [], []',
     'for c in (applicable if live["shared_safety"] == "PASS" else []):\n        failures, unknown = [], []'),
    ('R8_mode_mismatch_refuses', 'mismatch.append("mode")', 'pass'),
    ('R9_one_token_for_both_kinds', '_require(value["kind"] == kind and _hex64', '_require(_hex64'),
    ('R9_digest_not_bound_to_pins', '_require(c["evidence_sha256"] == digest(c["source_pins"]), "evidence version digest")',
     '_require(_hex64(c["evidence_sha256"]), "evidence version digest")'),
    ('R9_skipped_preference_untraced', 'result["preference_trace"].append({"field": "id", "skipped": cid, "reason": state})', 'pass'),
    # Additional single-line guard removals.
    ('P_shared_unknown_ignored', 'if live["shared_safety"] != "PASS":', 'if False:'),
    ('P_capability_mismatch_ignored', 'for field in ("capability", "domain", "context")', 'for field in ("domain", "context")'),
    ('P_context_mismatch_ignored', 'for field in ("capability", "domain", "context")', 'for field in ("capability", "domain")'),
    ('P_missing_required_setting_accepted', 'key not in c["settings"] or not same(c["settings"][key], value)',
     'key in c["settings"] and not same(c["settings"][key], value)'),
    ('P_confirmation_for_other_candidate_accepted', '"binding": binding, "candidate_id": selected["id"]}:',
     '"binding": binding, "candidate_id": (confirmation or {}).get("candidate_id")}:'),
    ('P_binding_ignores_assessment_fields', '"candidates": sorted(candidates, key=lambda c: c["id"]),',
     '"candidates": [{k: v for k, v in c.items() if k not in ("force_setpoint_n", "metrics", "assessment_basis", "qualifiers")} for c in sorted(candidates, key=lambda c: c["id"])],'),
    ('P_binding_ignores_request_force_metrics_settings', 'binding = digest({"request": request,',
     'binding = digest({"request": {k: v for k, v in request.items() if k not in ("force", "metrics", "required_settings")},'),
    ('P_metric_bound_exclusive', 'else (value > bound):', 'else (value >= bound):'),
    ('R3_lower_bound_inverted', 'if (value < bound) if requirement', 'if (value > bound) if requirement'),
    ('R3_lower_bound_exclusive', 'if (value < bound) if requirement', 'if (value <= bound) if requirement'),
    ('P_bool_counts_as_number', 'if type(value) not in (int, float):\n        return False', 'if not isinstance(value, (int, float)):\n        return False'),
    ('P_force_basis_not_required', '_require(type(force["basis"]) is str and bool(force["basis"].strip()),\n                 "force needs task-owner basis")', 'pass'),
    ('P_non_wipe_force_metric_allowed', '_require(not any(m["quantity"] == "normal_force_error" for m in request["metrics"]),\n                 "force requirement must use regulated capability")', 'pass'),
    ('P_local_safety_ignored', 'dict(l["checks"], safety=l["safety"], platform_safety=p["safety"])', 'dict(l["checks"], platform_safety=p["safety"])'),
    ('P_preference_completeness_ignored', 'if not {c[field] for c in ready} <= set(order):', 'if False:'),
    ('P_choice_ignored', 'if choice is not None:\n        if choice["binding"] != binding:', 'if False:\n        if choice["binding"] != binding:'),
    ('P_handover_contact_path_dropped', '"gesture_handover": {"valid_input", "visible_target", "path", "contact_authorized"},', '"gesture_handover": {"valid_input", "visible_target"},'),
    ('P_priority_candidate_validation_dropped', '_require(set(request["candidate_priority"]) <= set(ids), "unknown priority candidate")', 'pass'),
    ('P_live_inventory_superset_allowed', 'set(live["candidates"]) == set(ids)', 'set(live["candidates"]) >= set(ids)'),
    ('P_platform_inventory_superset_allowed', 'set(live["platforms"]) == platforms', 'set(live["platforms"]) >= platforms'),
    ('P_metric_ambiguity_ignored', 'if len(matches) != 1:', 'if not matches:'),
    ('P_duplicate_candidate_ids_allowed', '_require(len(set(ids)) == len(ids), "duplicate candidate identifier")', 'pass'),
    # Scope fields and metric-bound validation.
    ('R10_setpoint_scope_dropped', 'mismatch.append("setpoint")', 'pass'),
    ('R10_settings_scope_dropped', 'mismatch.append("settings")', 'pass'),
    ('R10_level_scope_dropped', 'mismatch.append("evidence_level")', 'pass'),
    ('R10_magnitude_bound_sign_unchecked', '_require(metric["comparison"] != "magnitude_at_most" or metric["bound"] >= 0, "magnitude bound must be non-negative")', 'pass'),
    # The depth check rejects deeply nested inputs before JSON recursion.
    # Removing only the recursion handler would therefore be an equivalent mutation.
    # Input depth and exact dictionary matching.
    ('R12_depth_unchecked', '_require(_depth([request, candidates, live]) <= MAX_DEPTH, "input nested too deeply")', 'pass'),
    ('R3_2_dict_keys_subset', 'return set(a) == set(b) and all(same(a[k], b[k]) for k in a)', 'return set(a) <= set(b) and all(same(a[k], b[k]) for k in a)'),
    # Binding, trace and field-validation checks.
    ('Q_same_list_length_ignored', 'return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))', 'return all(same(x, y) for x, y in zip(a, b))'),
    ('Q_binding_ignores_evidence_level', '"candidates": sorted(candidates, key=lambda c: c["id"]),',
     '"candidates": [{k: v for k, v in c.items() if k != "evidence_level"} for c in sorted(candidates, key=lambda c: c["id"])],'),
    ('Q_binding_ignores_request_scope_fields', 'binding = digest({"request": request,',
     'binding = digest({"request": {k: v for k, v in request.items() if k not in ("allowed_targets", "allowed_modes", "accepted_evidence_levels")},'),
    ('Q_binding_ignores_allowed_targets', 'binding = digest({"request": request,', 'binding = digest({"request": dict(request, allowed_targets=None),'),
    ('Q_binding_ignores_accepted_levels', 'binding = digest({"request": request,', 'binding = digest({"request": dict(request, accepted_evidence_levels=None),'),
    ('Q_binding_ignores_priorities', 'binding = digest({"request": request,', 'binding = digest({"request": dict(request, candidate_priority=None, mode_priority=None),'),
    ('Q_binding_ignores_platforms', '"live": live})', '"live": dict(live, platforms=None)})'),
    ('Q_qualifiers_validation_dropped', '_require(_strings(c["qualifiers"], False), "qualifiers")', 'pass'),
    ('Q_empty_source_pins_allowed', 'type(c["source_pins"]) is dict and bool(c["source_pins"])', 'type(c["source_pins"]) is dict'),
    ('Q_source_pin_values_not_hex', 'all(_text(k) and _hex64(v) for k, v in c["source_pins"].items())', 'all(_text(k) for k, v in c["source_pins"].items())'),
    ('Q_trace_basis_swapped', 'basis = request["mode_priority_basis" if field == "mode" else "candidate_priority_basis"]', 'basis = request["candidate_priority_basis"]'),
    ('Q_single_ready_trace_dropped', 'result["preference_trace"].append({"field": field, "applied": False, "reason": "single ready candidate"})', 'pass'),
    ('Q_incomplete_order_trace_dropped', 'result["preference_trace"].append({"field": field, "applied": False, "reason": "incomplete declared order"})', 'pass'),
    ('Q_excluded_choice_reason_merged', 'if choice["candidate_id"] not in {c["id"] for c in ready}:\n            return finish("ASK", "choice names',
     'if choice["candidate_id"] not in {c["id"] for c in ready}:\n            return finish("ASK", "choice stale: made for another request, assessment or snapshot"); finish("ASK", "choice names'),
    ('Q_skip_trace_not_applicable_mislabelled', 'state = ("not applicable" if cid in result["not_applicable"] else', 'state = ("unresolved" if cid in result["not_applicable"] else'),
    ('Q_mode_priority_subset_dropped', '_require(set(request["mode_priority"]) <= set(request["allowed_modes"]),\n             "priority names disallowed mode")', 'pass'),
    ('Q_force_minimum_positive_dropped', 'and 0 < force["minimum_n"] <= force["maximum_n"], "force range")', 'and force["minimum_n"] <= force["maximum_n"], "force range")'),
    ('Q_force_min_le_max_dropped', 'and 0 < force["minimum_n"] <= force["maximum_n"], "force range")', 'and 0 < force["minimum_n"], "force range")'),
    ('Q_candidate_nonwipe_force_allowed', '_require(c["force_setpoint_n"] is None, "candidate non-regulated force")', 'pass'),
    ('Q_declaration_candidate_id_unchecked', 'and _hex64(value["binding"]) and _text(value["candidate_id"]), kind + " fields")', 'and _hex64(value["binding"]), kind + " fields")'),
    ('Q_declaration_binding_unchecked', '_require(value["kind"] == kind and _hex64(value["binding"]) and', '_require(value["kind"] == kind and'),
    ('Q_candidate_positive_setpoint_dropped', '_require(_number(c["force_setpoint_n"]) and c["force_setpoint_n"] > 0,', '_require(_number(c["force_setpoint_n"]),'),
    ('Q_snapshot_id_text_unchecked', '_require(_text(live["snapshot_id"]), "live snapshot identity")', 'pass'),
    ('Q_evidence_ids_unchecked', '_require(_strings(c["evidence_ids"]), "evidence identifiers")', 'pass'),
    ('Q_priority_basis_type_unchecked', '_require(type(basis) is str, "preference basis type")', 'pass'),
]


def run_tests(directory):
    start = time.monotonic()
    p = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(directory), '-v'],
                       text=True, capture_output=True, timeout=120,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    return p, time.monotonic() - start


def build_bytes(seed):
    """Build the examples in a disposable copy under one hash seed and return the output hashes."""
    with tempfile.TemporaryDirectory(prefix='selector-determinism-') as temp:
        work = Path(temp) / 'packet'
        shutil.copytree(HERE.parent, work, ignore=shutil.ignore_patterns('verification', '__pycache__', '.git'))
        subprocess.run([sys.executable, '-B', 'build_examples.py'], cwd=work / 'selector', check=True,
                       capture_output=True, env=dict(os.environ, PYTHONHASHSEED=str(seed), PYTHONDONTWRITEBYTECODE='1'))
        return {n: hashlib.sha256((work / 'analysis' / n).read_bytes()).hexdigest()
                for n in ('CANDIDATE_MAPPING.json', 'DEVELOPMENT_EXAMPLES.json')}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = (HERE / 'selector.py').read_text()
    p, seconds = run_tests(HERE)
    log = p.stdout + p.stderr
    (OUT / 'TESTS.log').write_text(log)
    ran = re.search(r'^Ran (\d+) tests? in', log, re.M)
    if p.returncode or not ran:
        raise RuntimeError('baseline tests failed')
    results = []
    for name, before, after in MUTATIONS:
        if source.count(before) != 1:
            raise RuntimeError('mutation anchor not unique: ' + name)
        with tempfile.TemporaryDirectory(prefix='journal-selector-attack-') as temp:
            packet = Path(temp) / 'packet'
            shutil.copytree(HERE.parent, packet, ignore=shutil.ignore_patterns('verification', '__pycache__', '.git'))
            work = packet / 'selector'
            changed = source.replace(before, after)
            compile(changed, '<mutation>', 'exec')
            (work / 'selector.py').write_text(changed)
            result, elapsed = run_tests(work)
            mlog = result.stdout + result.stderr
            # Require an assertion failure and a complete test run.
            # Import failures alone do not count; other errors are recorded separately.
            mran = re.search(r'^Ran (\d+) tests? in', mlog, re.M)
            complete = bool(mran) and int(mran.group(1)) == int(ran.group(1))
            caught = result.returncode != 0 and '\nFAIL: ' in mlog and complete
            (OUT / ('mutation_' + name + '.log')).write_text(mlog)
            blocks = re.split(r'\n={50,}\n', mlog)
            converted = sum(1 for b in blocks if b.startswith('FAIL:') and 'AssertionError: unexpected ' in b)
            genuine = sum(1 for b in blocks if b.startswith('FAIL:') and 'AssertionError: unexpected ' not in b)
            results.append({'name': name, 'caught_by_assertion': caught, 'returncode': result.returncode,
                            'assertion_failures': genuine, 'converted_exceptions': converted, 'side_effect_errors': mlog.count('\nERROR: ')})
    seeds = {str(s): build_bytes(s) for s in (0, 1, 2, 3)}
    deterministic = len({json.dumps(v, sort_keys=True) for v in seeds.values()}) == 1
    record = {'status': 'LOCAL_VERIFICATION_ONLY', 'test_returncode': p.returncode, 'tests_ran': int(ran.group(1)),
              'mutation_count': len(results), 'mutations_caught': sum(r['caught_by_assertion'] for r in results),
              'mutations': results, 'build_bytes_by_hash_seed': seeds, 'build_bytes_deterministic': deterministic,
              'source_sha256': {n: hashlib.sha256((HERE / n).read_bytes()).hexdigest() for n in FILES},
              'native_trials': 0, 'decision_benefit_established': False}
    (OUT / 'VALIDATION.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: v for k, v in record.items() if k not in {'mutations', 'source_sha256', 'build_bytes_by_hash_seed'}},
                     indent=2, sort_keys=True))
    if not all(r['caught_by_assertion'] for r in results) or not deterministic:
        raise RuntimeError('mutation survived, failed without an assertion, or build bytes depend on the hash seed')


if __name__ == '__main__':
    main()
