"""Q2 improvement experiments reusing q2_core. Run from repository root."""
import sys,json,csv,gzip,hashlib,subprocess,time,platform,importlib.util
from pathlib import Path
from datetime import datetime,date
import numpy as np
import scipy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from q2_core import plan,execute,step,margin
from run_q2_poc import unit_checks
from prepare_q2_inputs import read_sources

def dump(p,x):
    p.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=lambda v:v.item() if hasattr(v,'item') else v,allow_nan=False)+'\n',encoding='utf-8')
def csvout(p,x):
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(x[0]));w.writeheader();w.writerows(x)
BASE=dict(name='control',forecast='mean7',q=.8,residual=28,target=6000,horizon=1,controller='greedy')
def variants():
    out=[BASE.copy()]
    for method in ['mean3','mean14','weighted','weekday']:
        out.append(dict(BASE,name=method,forecast=method))
    for n in [14,56]:out.append(dict(BASE,name=f'residual{n}',residual=n))
    for q in [.75,.775,.825,.85]:out.append(dict(BASE,name=f'q{q}',q=q))
    for target in [1200,3600,8400]:out.append(dict(BASE,name=f'target{target}',target=target))
    out.append(dict(BASE,name='horizon48',horizon=2))
    out.append(dict(BASE,name='planned_reserve',controller='planned_reserve'))
    return out

def predict(l,v,dates,i,method):
    assert i>0
    if method.startswith('mean'):
        n=int(method[4:]);return l[max(0,i-n):i].mean(0),v[max(0,i-n):i].mean(0)
    if method=='weighted':
        n=min(14,i);w=2.**(-np.arange(n-1,-1,-1)/3.);w/=w.sum()
        return w@l[i-n:i],w@v[i-n:i]
    weekday=date.fromisoformat(dates[i]).weekday()
    inds=[j for j in range(max(0,i-35),i) if date.fromisoformat(dates[j]).weekday()==weekday]
    if len(inds)<2:inds=list(range(max(0,i-7),i))
    return l[inds].mean(0),v[max(0,i-7):i].mean(0)

SUMS=['plan_kwh','emergency_kwh','plan_cost','emergency_cost','total_cost','paid_grid_spill_kwh','pv_spill_kwh']
def aggregate(ds,price):
    out={k:sum(r[k] for r in ds) for k in SUMS}
    out.update(days=len(ds),e_start=ds[0]['e_start'],e_end=ds[-1]['e_end'],
        emergency_slots=sum(r['emergency_slots'] for r in ds),milp_days=sum(r['solver']=='MILP' for r in ds))
    out['adjusted_cost']=out['total_cost']+.9*float(np.mean(price))*(out['e_start']-out['e_end'])
    return out

def main():
    out=ROOT/sys.argv[1];out.mkdir(parents=True,exist_ok=False)
    cfg=json.loads((ROOT/'configs/q2_full.json').read_text(encoding='utf-8'))
    src=ROOT/'experiments/q2-full-20260911-01/inputs.json'
    data=json.loads(src.read_text(encoding='utf-8'))
    assert read_sources(cfg)==data
    dates=data['dates'];l=np.array(data['load']);v=np.array(data['pv']);p=np.array(data['prices'])
    n=144;start=31;cut=120
    started=time.perf_counter()
    dump(out/'run.json',dict(code_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        started_at=datetime.now().astimezone().isoformat(),config=cfg,input_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
        input_source=str(src.relative_to(ROOT)),python=platform.python_version(),numpy=np.__version__,scipy=scipy.__version__,
        experiment_type='retrospective development; no new unseen holdout',variants=variants()))
    dump(out/'small_checks.json',unit_checks(cfg))
    # Same original warmup, including Jan 1 emergency-only cold start.
    energy=6000.
    for i in range(start):
        if i==0:g=np.zeros(n)
        else:
            lf,vf=predict(l,v,dates,i,'mean7')
            f,_=plan(p,lf,vf,energy,cfg);g=f['g']
        _,energy=execute(p,g,l[i],v[i],energy,cfg)
    initial=energy
    print('Shared initial',initial,flush=True)
    cache={}
    information=[]
    methods=['mean7','mean3','mean14','weighted','weekday']
    for method in methods:
        cache[method]={i:predict(l,v,dates,i,method) for i in range(1,len(dates))}
        for i in [31,120,243]:
            la=l.copy();va=v.copy();la[i:]+=999;va[i:]*=.2
            a=predict(la,va,dates,i,method)
            assert all(np.array_equal(x,y) for x,y in zip(a,cache[method][i]))
            information.append(dict(method=method,date=dates[i],future_perturbation_pass=True))
    dump(out/'information_checks.json',information)
    daily=[];specs=variants()
    def run(spec):
        name=spec['name'];ds=[];energy=initial
        c=dict(cfg,planned_terminal_energy=spec['target'],residual_days=spec['residual'],candidate_quantile=spec['q'])
        with gzip.open(out/(name+'_dispatch.csv.gz'),'wt',encoding='utf-8',newline='') as ledger, \
             gzip.open(out/(name+'_plans.jsonl.gz'),'wt',encoding='utf-8') as plans:
            writer=None
            for i in range(start,len(dates)):
                lf,vf=cache[spec['forecast']][i]
                residuals=[l[h]-v[h]-cache[spec['forecast']][h][0]+cache[spec['forecast']][h][1]
                           for h in range(max(1,i-spec['residual']),i)]
                extra=margin(residuals,c)
                h=spec['horizon']
                f,info=plan(np.tile(p,h),np.tile(lf+extra,h),np.tile(vf,h),energy,c)
                g=f['g'][:n]
                if spec['controller']=='greedy': rows,end=execute(p,g,l[i],v[i],energy,c)
                else:
                    rows=[];end=energy
                    for t in range(n):
                        cc=dict(c,energy_min=max(c['energy_min'],min(end,float(f['e'][t]))))
                        r=step(float(g[t]),float(l[i,t]),float(v[i,t]),end,cc)
                        r.update(slot=t,price=float(p[t]),plan_cost=float(p[t]*g[t]),
                                 emergency_cost=float(5*p[t]*r['emergency_kwh']))
                        r['total_cost']=r['plan_cost']+r['emergency_cost'];rows.append(r);end=r['e_end']
                for r in rows:
                    r.update(date=dates[i],strategy=name)
                    if writer is None:writer=csv.DictWriter(ledger,list(r));writer.writeheader()
                    writer.writerow(r)
                record=dict(date=dates[i],strategy=name,e_start=energy,e_end=end,
                    emergency_slots=sum(r['emergency_kwh']>1e-6 for r in rows),solver=info['formulation'])
                record.update({k:sum(r[k] for r in rows) for k in SUMS});ds.append(record)
                qrecord=dict(date=dates[i],e_start=energy,lf=lf.tolist(),vf=vf.tolist(),margin=extra.tolist(),
                    horizon=h,target=spec['target'],history_end=dates[i-1],flow={k:a.tolist() for k,a in f.items()},solver=info)
                plans.write(json.dumps(qrecord,ensure_ascii=False)+'\n')
                energy=end
        daily.extend(ds)
        total=dict(strategy=name,**aggregate(ds,p))
        selection=aggregate([r for r in ds if r['date']<'2025-05-01'],p)
        total['selection_score']=selection['adjusted_cost']
        print(name,round(total['total_cost'],2),'select',round(total['selection_score'],2),flush=True)
        csvout(out/'daily.csv',daily)
        return total
    totals=[run(s) for s in specs]
    # Combination is determined ONLY from Feb-Apr scores of predefined single-factor options.
    score={r['strategy']:r['selection_score'] for r in totals}
    choose=lambda names:min(names,key=lambda x:score[x])
    lookup={s['name']:s for s in specs}
    comb=dict(BASE,name='combined')
    selected={}
    for field,names in [
        ('forecast',['control','mean3','mean14','weighted','weekday']),
        ('residual',['control','residual14','residual56']),
        ('q',['control','q0.75','q0.775','q0.825','q0.85']),
        ('target',['control','target1200','target3600','target8400'])]:
        name=choose(names);selected[field]=name;comb[field]=lookup[name][field]
    dump(out/'combination_selection.json',dict(period='2025-02-01..2025-04-30',choices=selected,spec=comb))
    specs.append(comb);totals.append(run(comb))
    comb48=dict(comb,name='combined48',horizon=2)
    specs.append(comb48);totals.append(run(comb48))
    csvout(out/'summary.csv',totals);dump(out/'specs.json',specs)
    periods=[]
    for s in specs:
        for label,lo,hi in [('development','2025-02-01','2025-04-30'),('later_1','2025-05-01','2025-08-31'),('later_2','2025-09-01','2025-12-31')]:
            ds=[r for r in daily if r['strategy']==s['name'] and lo<=r['date']<=hi]
            periods.append(dict(strategy=s['name'],period=label,**aggregate(ds,p)))
    csvout(out/'periods.csv',periods)
    chosen=min(totals,key=lambda r:r['selection_score'])['strategy']
    dump(out/'selection.json',dict(selected_by_feb_apr=chosen,all_scores={r['strategy']:r['selection_score'] for r in totals},
         note='All results are retrospective; fixed-spec full-year candidate is not a prospective May-switch deployment.'))
    old=json.loads((ROOT/'experiments/q2-full-20260911-01/totals.json').read_text(encoding='utf-8'))
    original=next(r for r in old if r['strategy']=='operational')
    control=totals[0];err=max(abs(control[k]-original[k]) for k in SUMS+['e_start','e_end'])
    dump(out/'control_reproduction.json',dict(pass_=err<.01,max_error=err))
    assert err<.01
    dump(out/'completion.json',dict(seconds=time.perf_counter()-started,candidates=len(specs),days_per_candidate=334,
        selected=chosen,control_reproduction_pass=True))
if __name__=='__main__':main()
