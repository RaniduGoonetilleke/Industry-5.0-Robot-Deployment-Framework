"""Recalculate the 8 N force summaries and final joint-position ranges.

W1 is the scored four seconds of the wipe, W2 the complete eight-second circle,
and W3 the final two-second hold. Force is the negative world-x contact component.
Pad 0 is the right pad, targeted at 8 N; pad 1 is the left pad, targeted at 7 N.
Every value is checked against the supplied records. This uses reduced data and
does not rerun the simulation.

Run from the package root: python3 -B catalogue_r3/reproduce_8N_metrics.py"""
import sys
sys.dont_write_bytecode = True
import hashlib, json
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
records = json.loads((HERE/'FORCE_8N_RECORDS.json').read_text())['sites']
profiles = {p['id']: p for p in json.loads((HERE/'CANDIDATE_MAPPING_R3.json').read_text())['profiles']}
provenance = json.loads((HERE/'data/DATA_PROVENANCE_8N.json').read_text())['arrays']
rows = []
for site in 'ABCD':
    path = HERE/'data'/('%s_8N_reduced.npz' % site)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == provenance[path.name]['sha256'], path.name
    with np.load(path, allow_pickle=False) as a:
        phase, t = a['phase'], a['phase_t_s']; force = -a['contact_total_force_w_n'][:, :, 0]
        windows = {'W1': (phase == 'circle') & (t > 2.) & (t <= 6.), 'W2': phase == 'circle', 'W3': phase == 'measure_press_after'}
        assert [int(m.sum()) for m in windows.values()] == [800, 1600, 400]
        right = {w: force[m, 0] for w, m in windows.items()}
        got = {'W1_mean_n': right['W1'].mean(), 'W1_rms_error_n': np.sqrt(np.mean((right['W1']-8.)**2)),
               'W1_max_abs_error_n': np.max(np.abs(right['W1']-8.)), 'W2_min_n': right['W2'].min(), 'W2_max_n': right['W2'].max(),
               'W3_mean_n': right['W3'].mean(), 'left_W1_mean_n': force[windows['W1'], 1].mean(), 'left_W3_mean_n': force[windows['W3'], 1].mean(),
               'final_studied_range_rad': np.ptp(a['joint_pos_rad'][windows['W3']][:, a['studied_ids']], axis=0).max()}
    r = records[site]
    expected = {'W1_mean_n': r['W1_right']['mean_n'], 'W1_rms_error_n': r['W1_right']['rms_error_n'],
                'W1_max_abs_error_n': r['W1_right']['maximum_abs_error_n'], 'W2_min_n': r['W2_right_whole_circle']['minimum_n'],
                'W2_max_n': r['W2_right_whole_circle']['maximum_n'], 'W3_mean_n': r['W3_right_final_hold']['mean_n'],
                'left_W1_mean_n': r['left_means_n']['W1'], 'left_W3_mean_n': r['left_means_n']['W3'],
                'final_studied_range_rad': r['final_studied_position_range_max_rad']}
    for key in got:
        assert float(got[key]) == expected[key], (site, key, float(got[key]), expected[key])
    metric = {m['statistic']: m['value'] for m in profiles['G1_%s_8N' % site]['metrics'] if m['quantity'] in ('normal_force', 'normal_force_error')}
    assert (metric['mean'], metric['RMSE'], metric['maximum_absolute']) == (expected['W1_mean_n'], expected['W1_rms_error_n'], expected['W1_max_abs_error_n'])
    rows.append({'site': site, **{k: float(v) for k, v in got.items()}, 'equals_records_and_catalogue_exactly': True})
out = HERE.parent/'verification'; out.mkdir(exist_ok=True)
(out/'METRIC_REPRODUCTION_8N.json').write_text(json.dumps({'scope': __doc__, 'rows': rows}, indent=2)+'\n')
print(json.dumps(rows, indent=2))
