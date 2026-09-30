"""Recalculate the 7 N force summaries and final joint-position ranges.

The reduced arrays do not contain every field used in the original task checks.
These calculations check the saved measurements without rerunning the simulation."""
import sys
sys.dont_write_bytecode=True
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
profiles=json.loads((ROOT/'analysis/CANDIDATE_MAPPING.json').read_text())['profiles']
summary=json.loads((ROOT/'records/SPATIAL_RESULTS_SUMMARY.json').read_text())['locations']
keys={'B':'r080_a000','C':'r080_a180','D':'r080_a090'}
rows=[]
for site in 'ABCD':
    with np.load(ROOT/'data'/f'{site}_reduced.npz',allow_pickle=False) as a:
        mask=(a['phase']=='circle') & (a['phase_t_s']>2.) & (a['phase_t_s']<=6.)
        assert int(mask.sum())==800
        force=-a['contact_total_force_w_n'][mask,0,0]
        got={'mean':float(force.mean()),'rmse':float(np.sqrt(np.mean((force-7.)**2))),'max':float(np.max(np.abs(force-7.)))}
        prof=next(p for p in profiles if p.get('platform')=='G1' and p['target']==site)
        expected={m['statistic']:m['value'] for m in prof['metrics'] if m['quantity'] in ('normal_force','normal_force_error')}
        tolerance=0.0000005 if site=='A' else 1e-12
        assert abs(got['mean']-expected['mean'])<=tolerance
        assert abs(got['rmse']-expected['RMSE'])<=tolerance
        if site!='A': assert abs(got['max']-expected['maximum_absolute'])<=tolerance
        final=a['phase']=='measure_press_after'
        ranges=np.ptp(a['joint_pos_rad'][final][:,a['studied_ids']],axis=0)
        final_range=float(ranges.max())
        if site!='A': assert final_range==summary[keys[site]]['final_range_rad']
        rows.append({'site':site,'samples':int(mask.sum()),'force_mean_n':got['mean'],'force_rmse_n':got['rmse'],
                     'force_max_abs_error_n':got['max'] if site!='A' else None,
                     'A_max_policy':'not introduced; absent from accepted catalogue' if site=='A' else None,
                     'final_studied_joint_range_rad':final_range,'verified_against_catalogue':True})
(ROOT/'verification').mkdir(exist_ok=True)
(ROOT/'verification/METRIC_REPRODUCTION.json').write_text(json.dumps({'scope':__doc__,'rows':rows},indent=2)+'\n')
print(json.dumps(rows,indent=2))
