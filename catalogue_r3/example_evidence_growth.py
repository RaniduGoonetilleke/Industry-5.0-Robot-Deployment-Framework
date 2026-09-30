"""Show how decisions change when the four tested 8 N settings become available.

The same requests are evaluated with the 7 N catalogue and with the catalogue
containing both settings. Live checks and confirmations are synthetic inputs.
A permitted setpoint range describes requested settings, not instantaneous force.

Run from the package root: python3 -B catalogue_r3/example_evidence_growth.py"""
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'selector'))
from selector import select            # noqa: E402
from build_examples import live_for    # noqa: E402

REGISTERS = {'earlier (7 N only)': ROOT / 'analysis/CANDIDATE_MAPPING.json',
             'current (7 N and 8 N)': ROOT / 'catalogue_r3/CANDIDATE_MAPPING_R3.json'}
REQUESTS = (('exact 8 N', 8.0, 8.0), ('6-8 N range', 6.0, 8.0), ('exact 7.5 N', 7.5, 7.5), ('7.5-8 N range', 7.5, 8.0),
            ('exact 7 N', 7.0, 7.0))


def request(lo, hi, site):
    return {'id': 'owner_request_%g_%g_N_at_%s' % (lo, hi, site), 'capability': 'regulated_wipe', 'domain': 'simulation',
            'context': 'supported_original_wall_point_pilot', 'allowed_targets': [site],
            'allowed_modes': ['virtual_confirmation_then_bounded_wipe'], 'accepted_evidence_levels': ['simulation'],
            'force': {'minimum_n': lo, 'maximum_n': hi,
                      'basis': 'Illustrative owner-permitted right-pad setpoint range; not a cleaning requirement'},
            'required_settings': {}, 'metrics': [], 'mode_priority': [], 'mode_priority_basis': '',
            'candidate_priority': [], 'candidate_priority_basis': ''}


def decide(req, catalogue):
    live = live_for(catalogue, 'PASS')      # Synthetic: every live fact assumed to pass, for a software check only
    first = select(req, catalogue, live)
    out = {'decision': first['decision'], 'configuration': first['configuration']['id'] if first['configuration'] else None,
           'remaining': first['remaining'], 'reason': first['reasons'][-1]}
    if first['decision'] == 'CONFIRM':
        second = select(req, catalogue, live, confirmation={'kind': 'confirmation', 'binding': first['binding'],
                                                            'candidate_id': first['configuration']['id']})
        out['after_synthetic_confirmation'] = second['decision']
    return out


def main():
    catalogues = {name: json.loads(path.read_text())['profiles'] for name, path in REGISTERS.items()}
    result = {'registers': {name: {'file': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                   'profiles': [c['id'] for c in catalogues[name]]} for name, path in REGISTERS.items()},
              'live_facts': 'synthetic, all PASS; confirmations synthetic', 'rows': []}
    for label, lo, hi in REQUESTS:
        for site in 'ABCD':
            req = request(lo, hi, site)
            result['rows'].append({'request': label, 'setpoint_range_n': [lo, hi], 'site': site,
                                   **{name: decide(req, cat) for name, cat in catalogues.items()}})
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
