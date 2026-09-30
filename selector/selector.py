"""Choose a supported robot configuration from supplied evidence and live checks.

The caller provides the capability assessments. Recorded metrics describe the
tested setting; they do not guarantee a future outcome. Choices and confirmations
refer to the exact request and current facts. The function returns a decision
and its reasons. It does not communicate with a robot or write files."""
from copy import deepcopy
from hashlib import sha256
import json
import math

EVIDENCE = {"capability", "domain", "context", "measurement", "mode"}
STATES = {"PASS", "FAIL", "UNKNOWN", "CONFLICTING"}
LIVE = {
    "regulated_wipe": {"valid_input", "visible_target", "path", "contact_authorized"},
    "gesture_handover": {"valid_input", "visible_target", "path", "contact_authorized"},
    "target_detection": {"valid_input", "visible_target"},
}
CAPABILITIES = set(LIVE)
AVAILABILITY = {"AVAILABLE", "BUSY", "UNKNOWN"}
COMPARISONS = {"value_at_most", "magnitude_at_most", "value_at_least"}
# Evidence categories used by the supplied records.
EVIDENCE_LEVELS = {
    "physical-published",
    "physical-published-data (released log, re-analysed here)",
    "physical-unpublished-log (local trial-day console log, not released)",
    "physical-report",
    "simulation",
    "simulation-model offline screen (no physics)",
}


MAX_DEPTH = 32   # Deepest container nesting accepted in any input (the schema itself needs about 6)


def _depth(value):
    """Find the nesting depth without using recursive calls."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        item, level = stack.pop()
        if type(item) in (dict, list):
            deepest = max(deepest, level)
            if level > MAX_DEPTH:
                return level
            stack.extend((x, level + 1) for x in (item.values() if type(item) is dict else item))
    return deepest


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return type(value) is str and bool(value)


def _member(value, allowed):
    return type(value) is str and value in allowed


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _strings(value, nonempty=True):
    return (type(value) is list and (bool(value) or not nonempty)
            and all(_text(x) for x in value)
            and len(set(value)) == len(value))


def _hex64(value):
    return type(value) is str and len(value) == 64 and all(x in "0123456789abcdef" for x in value)


def _declaration(value, kind):
    _require(type(value) is dict and set(value) == {"kind", "binding", "candidate_id"}, kind + " schema")
    _require(value["kind"] == kind and _hex64(value["binding"]) and _text(value["candidate_id"]), kind + " fields")


def _states(values, keys):
    return (type(values) is dict and set(values) == keys
            and all(_member(v, STATES) for v in values.values()))


def same(a, b):
    """Exact JSON identity: same type at every level (1 is not True, 7 is not 7.0, -0.0 is not 0.0), same keys, same
    order of lists."""
    if type(a) is not type(b):
        return False
    if type(a) is float and math.copysign(1.0, a) != math.copysign(1.0, b):
        return False
    if type(a) is dict:
        return set(a) == set(b) and all(same(a[k], b[k]) for k in a)
    if type(a) is list:
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def _metric(metric, required=False):
    fields = {"quantity", "statistic", "window", "side", "unit", "scope"}
    fields |= {"bound", "comparison"} if required else {"value"}
    _require(type(metric) is dict and set(metric) == fields, "metric schema")
    for name in ("quantity", "statistic", "window", "side", "unit", "scope"):
        _require(_text(metric[name]), "metric definition")
    if required:
        _require(_member(metric["comparison"], COMPARISONS), "metric comparison")
        _require(_number(metric["bound"]), "finite numeric metric")
        _require(metric["comparison"] != "magnitude_at_most" or metric["bound"] >= 0, "magnitude bound must be non-negative")
    else:
        _require(_number(metric["value"]), "finite numeric metric")


def _definition(metric):
    return {k: metric[k] for k in ("quantity", "statistic", "window", "side", "unit", "scope")}


def validate(request, candidates, live):
    _require(_depth([request, candidates, live]) <= MAX_DEPTH, "input nested too deeply")
    rkeys = {"id", "capability", "domain", "context", "allowed_targets", "allowed_modes",
             "accepted_evidence_levels", "force", "required_settings", "metrics",
             "mode_priority", "mode_priority_basis", "candidate_priority", "candidate_priority_basis"}
    _require(type(request) is dict and set(request) == rkeys, "request schema")
    _require(_member(request["capability"], CAPABILITIES), "unsupported capability")
    for name in ("id", "domain", "context"):
        _require(_text(request[name]), "request field")
    for name in ("allowed_targets", "allowed_modes", "accepted_evidence_levels"):
        _require(_strings(request[name]), "nonempty declared " + name)
    _require(set(request["accepted_evidence_levels"]) <= EVIDENCE_LEVELS, "unknown evidence level")
    for name in ("mode_priority", "candidate_priority"):
        _require(_strings(request[name], False), "priority schema")
    _require(set(request["mode_priority"]) <= set(request["allowed_modes"]),
             "priority names disallowed mode")
    for name in ("mode_priority", "candidate_priority"):
        basis = request[name + "_basis"]
        _require(type(basis) is str, "preference basis type")
        if request[name]:
            _require(bool(basis.strip()), "preference needs basis")
    _require(type(request["required_settings"]) is dict, "settings schema")
    _require(type(request["metrics"]) is list, "metrics schema")
    for m in request["metrics"]:
        _metric(m, True)
    force = request["force"]
    if request["capability"] == "regulated_wipe":
        _require(type(force) is dict and set(force) == {"minimum_n", "maximum_n", "basis"},
                 "wiping needs force requirement")
        _require(_number(force["minimum_n"]) and _number(force["maximum_n"])
                 and 0 < force["minimum_n"] <= force["maximum_n"], "force range")
        _require(type(force["basis"]) is str and bool(force["basis"].strip()),
                 "force needs task-owner basis")
    else:
        _require(force is None, "force NOT_APPLICABLE only for non-regulated capability")
        _require(not any(m["quantity"] == "normal_force_error" for m in request["metrics"]),
                 "force requirement must use regulated capability")
    _require(type(candidates) is list, "candidate list")
    ids = []
    ckeys = {"id", "platform", "capability", "domain", "context", "target", "mode",
             "settings", "force_setpoint_n", "metrics", "checks", "evidence_ids", "evidence_level",
             "assessment_basis", "qualifiers", "source_pins", "evidence_sha256", "confirmation_required"}
    for c in candidates:
        _require(type(c) is dict and set(c) == ckeys, "candidate schema")
        for key in ("id", "platform", "domain", "context", "target", "mode", "assessment_basis"):
            _require(_text(c[key]), "candidate field")
        _require(_member(c["capability"], CAPABILITIES), "candidate capability")
        _require(_member(c["evidence_level"], EVIDENCE_LEVELS), "candidate evidence level")
        _require(_states(c["checks"], EVIDENCE), "evidence checks")
        _require(_strings(c["evidence_ids"]), "evidence identifiers")
        _require(_strings(c["qualifiers"], False), "qualifiers")
        _require(type(c["source_pins"]) is dict and bool(c["source_pins"])
                 and all(_text(k) and _hex64(v) for k, v in c["source_pins"].items()), "source pins")
        _require(c["evidence_sha256"] == digest(c["source_pins"]), "evidence version digest")
        _require(type(c["confirmation_required"]) is bool, "confirmation type")
        _require(type(c["settings"]) is dict and type(c["metrics"]) is list, "candidate values")
        for m in c["metrics"]:
            _metric(m)
        if c["capability"] == "regulated_wipe":
            _require(_number(c["force_setpoint_n"]) and c["force_setpoint_n"] > 0,
                     "candidate force")
        else:
            _require(c["force_setpoint_n"] is None, "candidate non-regulated force")
        ids.append(c["id"])
    _require(len(set(ids)) == len(ids), "duplicate candidate identifier")
    _require(set(request["candidate_priority"]) <= set(ids), "unknown priority candidate")
    _require(type(live) is dict and set(live) == {"snapshot_id", "shared_safety", "platforms", "candidates"},
             "live schema")
    _require(_text(live["snapshot_id"]), "live snapshot identity")
    _require(_member(live["shared_safety"], STATES), "shared safety state")
    platforms = {c["platform"] for c in candidates}
    _require(type(live["platforms"]) is dict and set(live["platforms"]) == platforms, "live platform inventory")
    for name in platforms:
        p = live["platforms"][name]
        _require(type(p) is dict and set(p) == {"availability", "safety"}, "platform live schema")
        _require(_member(p["availability"], AVAILABILITY) and _member(p["safety"], STATES), "platform live states")
    _require(type(live["candidates"]) is dict and set(live["candidates"]) == set(ids),
             "live candidate inventory")
    for c in candidates:
        l = live["candidates"][c["id"]]
        _require(type(l) is dict and set(l) == {"safety", "checks"}, "local live schema")
        _require(_member(l["safety"], STATES) and _states(l["checks"], LIVE[c["capability"]]), "local checks")
    # Reject non-JSON values, NaN and Infinity even in optional settings.
    try:
        digest([request, candidates, live])
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError("non-JSON input: %s" % type(error).__name__) from None


def select(request, candidates, live, *, choice=None, confirmation=None):
    """Return an offline decision and its reasons for the current inputs.
    
    Choices and confirmations must refer to this request, catalogue and live state.
    Changed observations require new declarations. The same declaration can be reused
    while those inputs stay unchanged; it is not an authenticated or one-use permission."""
    validate(request, candidates, live)
    if choice is not None:
        _declaration(choice, "choice")
    if confirmation is not None:
        _declaration(confirmation, "confirmation")
    binding = digest({"request": request, "candidates": sorted(candidates, key=lambda c: c["id"]),
                      "live": live})
    result = {"decision": None, "configuration": None, "remaining": [], "reasons": [],
              "candidate_reasons": {}, "not_applicable": {}, "preference_trace": [], "binding": binding,
              "release_authority": False, "dispatch_implemented": False,
              "facts_verified_by_code": False}

    def finish(decision, reason, pool=(), selected=None):
        result["decision"] = decision
        result["reasons"].append(reason)
        result["remaining"] = sorted(c["id"] for c in pool)
        result["configuration"] = deepcopy(selected)
        return result

    # Report out-of-scope options separately from failed or unresolved options.
    applicable = []
    for c in sorted(candidates, key=lambda x: x["id"]):
        mismatch = [field for field in ("capability", "domain", "context") if c[field] != request[field]]
        if c["target"] not in request["allowed_targets"]:
            mismatch.append("target")
        if c["mode"] not in request["allowed_modes"]:
            mismatch.append("mode")
        if c["evidence_level"] not in request["accepted_evidence_levels"]:
            mismatch.append("evidence_level")
        if any(key not in c["settings"] or not same(c["settings"][key], value)
               for key, value in request["required_settings"].items()):
            mismatch.append("settings")
        f = request["force"]
        if f is not None and c["capability"] == "regulated_wipe" and not f["minimum_n"] <= c["force_setpoint_n"] <= f["maximum_n"]:
            mismatch.append("setpoint")
        if mismatch:
            result["not_applicable"][c["id"]] = sorted(mismatch)
        else:
            applicable.append(c)

    # Check every applicable option so the result includes all blocking reasons.
    ready, waiting, unresolved = [], [], []
    for c in applicable:
        failures, unknown = [], []
        failures += ["evidence:" + k for k, v in c["checks"].items() if v == "FAIL"]
        unknown += ["evidence:" + k for k, v in c["checks"].items() if v in {"UNKNOWN", "CONFLICTING"}]
        for requirement in request["metrics"]:
            definition = _definition(requirement)
            matches = [m for m in c["metrics"] if _definition(m) == definition]
            label = digest(definition)[:12]
            if len(matches) != 1:
                unknown.append("missing or ambiguous metric:" + label)
            else:
                value, bound = matches[0]["value"], requirement["bound"]
                if requirement["comparison"] == "magnitude_at_most":
                    value = abs(value)
                if (value < bound) if requirement["comparison"] == "value_at_least" else (value > bound):
                    failures.append("recorded metric outside requested bound:" + label)
        p = live["platforms"][c["platform"]]
        l = live["candidates"][c["id"]]
        states = dict(l["checks"], safety=l["safety"], platform_safety=p["safety"])
        for key in sorted(states):
            if states[key] == "FAIL":
                failures.append("live:" + key)
            elif states[key] != "PASS":
                unknown.append("live:" + key)
        if p["availability"] == "UNKNOWN":
            unknown.append("platform availability unknown")
        result["candidate_reasons"][c["id"]] = {"failed": sorted(failures), "unresolved": sorted(unknown),
                                                 "platform_busy": p["availability"] == "BUSY"}
        if failures:
            continue
        if unknown:
            unresolved.append(c)
        elif p["availability"] == "BUSY":
            waiting.append(c)
        else:
            ready.append(c)
    if live["shared_safety"] == "FAIL":
        return finish("REFUSE", "shared work-area hazard")
    if live["shared_safety"] != "PASS":
        return finish("WITHHOLD", "shared safety unresolved")
    if not applicable:
        return finish("WITHHOLD", "no record for the requested capability, domain, context, target, mode, settings, setpoint "
                                  "and evidence level")
    if not ready:
        if waiting:
            return finish("WAIT", "supported configurations are on a busy platform", waiting)
        if unresolved:
            return finish("WITHHOLD", "required evidence/live facts missing or conflicting", unresolved)
        return finish("REFUSE", "every applicable configuration fails a guard")

    ready_ids = {c["id"] for c in ready}
    for cid in request["candidate_priority"]:
        if cid not in ready_ids:
            state = ("not applicable" if cid in result["not_applicable"] else
                     "failed" if result["candidate_reasons"][cid]["failed"] else
                     "unresolved" if result["candidate_reasons"][cid]["unresolved"] else "robot busy")
            result["preference_trace"].append({"field": "id", "skipped": cid, "reason": state})
    for field, order in (("mode", request["mode_priority"]), ("id", request["candidate_priority"])):
        if not order:
            continue
        if len(ready) == 1:
            result["preference_trace"].append({"field": field, "applied": False, "reason": "single ready candidate"})
            continue
        if not {c[field] for c in ready} <= set(order):
            result["preference_trace"].append({"field": field, "applied": False, "reason": "incomplete declared order"})
            continue
        best = min(order.index(c[field]) for c in ready)
        ready = [c for c in ready if order.index(c[field]) == best]
        basis = request["mode_priority_basis" if field == "mode" else "candidate_priority_basis"]
        result["preference_trace"].append({"field": field, "applied": True, "basis": basis})
    if choice is not None:
        if choice["binding"] != binding:
            return finish("ASK", "choice stale: made for another request, assessment or snapshot", ready)
        if choice["candidate_id"] not in {c["id"] for c in ready}:
            return finish("ASK", "choice names a configuration that is not among the remaining options", ready)
        ready = [c for c in ready if c["id"] == choice["candidate_id"]]
    if len(ready) != 1:
        return finish("ASK", "several supported options; explicit preference/choice needed", ready)
    selected = ready[0]
    if selected["confirmation_required"]:
        if confirmation != {"kind": "confirmation", "binding": binding, "candidate_id": selected["id"]}:
            return finish("CONFIRM", "confirm this exact task/configuration and current snapshot", ready, selected)
    return finish("SELECT", "offline selection within supplied assessments; no dispatch", ready, selected)
