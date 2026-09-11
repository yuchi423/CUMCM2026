"""Independently assemble the textbook Q1 LP and compare a saved strict dispatch.

No import of the production constraint builder. All energies are kWh.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
import openpyxl
from scipy.optimize import linprog

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True);args=parser.parse_args()
    run=args.run.resolve()
    if not run.is_relative_to((ROOT/'experiments').resolve()): raise ValueError('Run must be inside experiments')
    output=run/'independent_lp_check.json'
    if output.exists(): raise FileExistsError(output)
    book=openpyxl.load_workbook(ROOT/'data/raw/附件1.xlsx',read_only=True,data_only=True)
    data=np.array([r[1:] for r in book.active.iter_rows(min_row=2,values_only=True)],float);book.close()
    p,l,v=data.T;l=l/6;v=v/6;n=len(p)
    # Separate E0 and En variables so terminal equality is independently represented.
    objective=np.zeros(5*n+1);objective[:n]=p;equalities=[];rhs=[]
    for i in range(n):
        row=np.zeros(5*n+1);row[i]=1;row[n+i]=-1;row[2*n+i]=1;row[3*n+i]=-1
        equalities.append(row);rhs.append(l[i]-v[i])
        row=np.zeros(5*n+1);row[4*n+i+1]=1;row[4*n+i]=-1;row[n+i]=-.9;row[2*n+i]=1/.9
        equalities.append(row);rhs.append(0)
    bounds=[(0,None)]*n+[(0,5000/6)]*(2*n)+[(0,None)]*n+[(1200,10800)]*(n+1)
    bounds[4*n]=(6000,6000)
    row=np.zeros(5*n+1);row[-1]=1;row[4*n]=-1;equalities.append(row);rhs.append(0)
    result=linprog(objective,A_eq=equalities,b_eq=rhs,bounds=bounds,method='highs')
    if not result.success: raise RuntimeError(result.message)
    with (run/'results/end_rectangle/dispatch.csv').open(encoding='utf-8-sig',newline='') as stream:
        strict=list(csv.DictReader(stream))
    money=sum(float(row['price_yuan_kwh'])*float(row['grid_kwh']) for row in strict)
    pv_surplus_topup=[row for row in strict if float(row['pv_kwh'])>float(row['load_kwh'])+1e-6 and float(row['grid_charge_ac_kwh'])>1e-6]
    comparison={'independent_lp_cost_yuan':float(result.fun),'saved_strict_dispatch_cost_yuan':money,
                'difference_yuan':money-float(result.fun),'match_within_0_01_yuan':abs(money-result.fun)<.01,
                'independent_state0':float(result.x[4*n]),'independent_state24':float(result.x[-1]),
                'independent_mutual_slots':int(np.sum((result.x[n:2*n]>1e-6)&(result.x[2*n:3*n]>1e-6))),
                'input_sha256':hashlib.sha256((ROOT/'data/raw/附件1.xlsx').read_bytes()).hexdigest(),
                'strict_grid_topup_during_pv_surplus_kwh':sum(float(row['grid_charge_ac_kwh']) for row in pv_surplus_topup),
                'strict_topup_slots':pv_surplus_topup,
                'explanation':'LP has fewer source/mode restrictions; audited strict dispatch attains its lower bound. Original runs incorrectly prohibited grid top-up during PV surplus.'}
    assert comparison['match_within_0_01_yuan']
    assert len(pv_surplus_topup)>0
    output.write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in comparison.items() if k!='strict_topup_slots'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
