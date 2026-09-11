"""Independent output/source audit; does not import optimizer or executor."""
from pathlib import Path
import sys,json,csv,gzip,math
from collections import defaultdict
from datetime import date
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
def readcsv(p):
    with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def main(run):
    specs=json.loads((run/'specs.json').read_text(encoding='utf-8'))
    src=json.loads((ROOT/'experiments/q2-full-20260911-01/inputs.json').read_text(encoding='utf-8'))
    dates=src['dates'];indices={d:i for i,d in enumerate(dates)}
    l=np.array(src['load']);v=np.array(src['pv']);p=np.array(src['prices'])
    cache={}
    for method in set(s['forecast'] for s in specs):
        cache[method]={}
        for i in range(1,365):
            if method.startswith('mean'):
                js=list(range(max(0,i-int(method[4:])),i));ws=np.ones(len(js))/len(js);jv=js;wv=ws
            elif method=='weighted':
                js=list(range(max(0,i-14),i));ws=np.array([2**(-(i-1-j)/3) for j in js]);ws/=sum(ws);jv=js;wv=ws
            else:
                js=[j for j in range(max(0,i-35),i) if date.fromisoformat(dates[j]).weekday()==date.fromisoformat(dates[i]).weekday()]
                if len(js)<2:js=list(range(max(0,i-7),i))
                ws=np.ones(len(js))/len(js);jv=list(range(max(0,i-7),i));wv=np.ones(len(jv))/len(jv)
            cache[method][i]=(np.sum(l[js]*ws[:,None],axis=0),np.sum(v[jv]*wv[:,None],axis=0))
    daily=readcsv(run/'daily.csv');errors=defaultdict(float);count=0
    def err(k,x):errors[k]=max(errors[k],float(np.max(np.abs(x))))
    for spec in specs:
        name=spec['name'];plans={}
        with gzip.open(run/(name+'_plans.jsonl.gz'),'rt',encoding='utf-8') as f:
            for line in f:
                rec=json.loads(line);i=indices[rec['date']];plans[rec['date']]=rec
                lf,vf=cache[spec['forecast']][i]
                residuals=np.array([l[j]-v[j]-cache[spec['forecast']][j][0]+cache[spec['forecast']][j][1]
                                    for j in range(max(1,i-spec['residual']),i)])
                m=np.repeat([max(0,float(np.quantile(residuals[:,t:t+6],spec['q']))) for t in range(0,144,6)],6)
                err('forecast',lf-rec['lf']);err('forecast',vf-rec['vf']);err('margin',m-rec['margin'])
                assert rec['history_end']==dates[i-1]
                a={k:np.array(z) for k,z in rec['flow'].items()};h=spec['horizon']
                prev=np.r_[rec['e_start'],a['e'][:-1]];sur=np.maximum(np.tile(vf-lf-m,h),0)
                err('plan_balance',a['g']+np.tile(vf-lf-m,h)+a['d']-a['c']-a['s'])
                err('plan_state',a['e']-prev-.9*a['c']+a['d']/.9)
                err('plan_terminal',a['e'][-1]-spec['target'])
                err('plan_priority',sur-a['s']-np.minimum(sur,np.minimum(5000/6,(10800-prev)/.9)))
                assert not np.any((a['c']>1e-6)&(a['d']>1e-6))
                assert min(a['e'])>=1200-1e-6 and max(a['e'])<=10800+1e-6
                assert max(a['c'])<=5000/6+1e-6 and max(a['d'])<=5000/6+1e-6
                err('plan_objective',np.tile(p,h)@a['g']-rec['solver']['objective'])
        groups=defaultdict(lambda:defaultdict(float));last=10800.;slots=defaultdict(int)
        with gzip.open(run/(name+'_dispatch.csv.gz'),'rt',encoding='utf-8',newline='') as f:
            for row in csv.DictReader(f):
                d=row.pop('date');assert row.pop('strategy')==name
                x={k:float(z) for k,z in row.items()};t=int(x['slot']);i=indices[d];rec=plans[d]
                assert t==slots[d];slots[d]+=1;count+=1
                err('source_load',x['load_kwh']-l[i,t]);err('source_pv',x['pv_kwh']-v[i,t])
                err('fixed_plan',x['plan_kwh']-rec['flow']['g'][t]);err('continuity',x['e_start']-last)
                err('state',x['e_end']-last-.9*x['charge_kwh']+x['discharge_kwh']/.9)
                g=x['plan_kwh'];b=x['emergency_kwh'];pv=x['pv_kwh'];ld=x['load_kwh'];c=x['charge_kwh'];ds=x['discharge_kwh']
                err('balance',g+b+pv+ds-ld-c-x['pv_spill_kwh']-x['paid_grid_spill_kwh'])
                err('priority',x['pv_charge_kwh']-min(max(pv-ld,0),5000/6,max(0,(10800-last)/.9)))
                err('grid_charge',x['grid_charge_kwh']-min(max(0,g-max(ld-pv,0)),max(0,min(5000/6,(10800-last)/.9)-x['pv_charge_kwh'])))
                floor=1200 if spec['controller']=='greedy' else max(1200,min(last,rec['flow']['e'][t]))
                err('discharge_rule',ds-min(max(0,ld-pv-g),5000/6,max(0,last-floor)*.9))
                err('emergency',b-max(0,ld-pv-g-ds))
                assert not(c>1e-6 and ds>1e-6) and not(c>1e-6 and b>1e-6)
                assert 1200-1e-6<=x['e_end']<=10800+1e-6 and max(c,ds)<=5000/6+1e-6
                err('cost',x['total_cost']-p[t]*(g+5*b))
                err('plan_cost',x['plan_cost']-p[t]*g);err('emergency_cost',x['emergency_cost']-5*p[t]*b)
                last=x['e_end']
                for k in ['plan_kwh','emergency_kwh','plan_cost','emergency_cost','total_cost','paid_grid_spill_kwh','pv_spill_kwh']:groups[d][k]+=x[k]
            assert len(slots)==334 and all(n==144 for n in slots.values())
        for r in [r for r in daily if r['strategy']==name]:
            for k,total in groups[r['date']].items():err('daily_'+k,float(r[k])-total)
        print('audit',name,flush=True)
    summary=readcsv(run/'summary.csv')
    for s in summary:
        subset=[r for r in daily if r['strategy']==s['strategy']]
        err('total',float(s['total_cost'])-math.fsum(float(r['total_cost']) for r in subset))
        subset=[r for r in subset if r['date']<'2025-05-01']
        score=math.fsum(float(r['total_cost']) for r in subset)+.9*float(np.mean(p))*(float(subset[0]['e_start'])-float(subset[-1]['e_end']))
        err('selection_score',score-float(s['selection_score']))
    result=dict(pass_=bool(max(errors.values())<1e-6),rows=count,strategies=len(specs),errors=dict(errors),
                checks='Independent forecasts, margins, full-horizon planned physics, actual source/ledger/priority/execution/cost and selection scores')
    (run/'independent_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    assert result['pass_'],result
if __name__=='__main__':main(ROOT/sys.argv[1])
