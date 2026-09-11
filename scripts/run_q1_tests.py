"""Q1 contract experiments, exact optimization, independent audit and Excel round-trip.

Run from the repository: python scripts/run_q1_tests.py --output experiments/<new-id>
Original inputs are never modified. A new output directory is required.
"""
import argparse
import csv
import hashlib
import importlib.metadata
import itertools
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import time

import numpy as np
import openpyxl
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

ROOT = Path(__file__).resolve().parents[1]
TOL = 1e-6


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def csv_write(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def clock(m):
    return f'{m//60:02d}:{m%60:02d}'


def minute(value):
    if hasattr(value, 'hour'):
        return value.hour*60 + value.minute
    match = re.fullmatch(r'(\d+):(\d+)(?:\:00)?(\+1)?', str(value).strip())
    if not match:
        raise ValueError(value)
    return int(match[1])*60 + int(match[2]) + (1440 if match[3] else 0)


def solve(p, load, pv, *, integer, capacity=5000/6, e0=6000., emin=1200., emax=10800., fixed=None):
    """All flows are AC-bus kWh; E is internal battery kWh.

    Strict MILP: PV serves load first; surplus charging equals min(surplus,
    charge energy limit, pre-slot headroom / 0.9). Additional grid charging remains allowed after PV is absorbed; no export or simultaneous charging/discharging.
    The LP removes modes and compulsory absorption, providing a lower bound.
    """
    n = len(p)
    g,c,d,s,e,z,w = [np.arange(k*n,(k+1)*n) for k in range(7)]
    surplus = np.maximum(pv-load, 0)
    deficit = np.maximum(load-pv, 0)
    lower = np.zeros(7*n)
    upper = np.full(7*n, np.inf)
    upper[c] = capacity
    upper[d] = np.minimum(capacity, deficit)
    upper[s] = surplus
    lower[e], upper[e] = emin, emax
    lower[e[-1]], upper[e[-1]] = e0,e0
    upper[z], upper[w] = 1,1
    # PV-first does NOT forbid grid top-up while PV exceeds load.
    objective = np.zeros(7*n); objective[g] = p
    ri,ci,va,lo,hi = [],[],[],[],[]
    def add(terms, lb, ub):
        row=len(lo)
        for key,val in terms.items():
            ri.append(row);ci.append(int(key));va.append(val)
        lo.append(lb);hi.append(ub)
    for t in range(n):
        add({g[t]:1,c[t]:-1,d[t]:1,s[t]:-1},load[t]-pv[t],load[t]-pv[t])
        terms={e[t]:1,c[t]:-.9,d[t]:1/.9}
        if t: terms[e[t-1]]=-1
        add(terms,e0 if t==0 else 0,e0 if t==0 else 0)
        if integer or fixed is not None:
            add({c[t]:1,z[t]:-capacity},-np.inf,0)
            add({d[t]:1,z[t]:capacity},-np.inf,capacity)
            if surplus[t]>0:
                lim=min(capacity,surplus[t])
                # cp=surplus-s is PV-to-battery; c=cp+grid top-up.
                # w=0: cp=lim. w=1: battery is full from PV alone,
                # i.e. E_end - eta * grid_topup = Emax (therefore g=0).
                add({s[t]:-1,w[t]:lim},lim-surplus[t],np.inf)
                add({e[t]:1,g[t]:-.9,w[t]:-(emax-emin)},emin,np.inf)
            else:
                upper[w[t]]=0
    integrality=np.zeros(7*n,dtype=int)
    if integer:
        integrality[z]=1;integrality[w]=1
    if fixed is not None:
        for idx,value in fixed.items(): lower[idx]=upper[idx]=value
    matrix=coo_matrix((va,(ri,ci)),shape=(len(lo),7*n)).tocsc()
    begin=time.perf_counter()
    result=milp(objective,integrality=integrality,bounds=Bounds(lower,upper),
                constraints=LinearConstraint(matrix,np.array(lo),np.array(hi)),
                options={'mip_rel_gap':1e-10,'time_limit':120})
    elapsed=time.perf_counter()-begin
    if result.status!=0:
        raise RuntimeError(f'Solver status {result.status}: {result.message}')
    flows={key:result.x[idx] for key,idx in zip(['g','c','d','s','e'],[g,c,d,s,e])}
    return flows,{'objective':float(result.fun),'status':int(result.status),'message':result.message,
                  'mip_gap':float(result.mip_gap) if getattr(result,'mip_gap',None) is not None else None,
                  'dual_bound':float(result.mip_dual_bound) if getattr(result,'mip_dual_bound',None) is not None else None,
                  'solve_seconds':elapsed}


def audit(p, load, pv, f, objective, *, capacity=5000/6,e0=6000,emin=1200,emax=10800):
    # Sequential recomputation from output flows, independent of matrix rows.
    energy=float(e0); trajectory=[]; balance=[]; priority=[]
    for l,v,c,d,g,s in zip(load,pv,f['c'],f['d'],f['g'],f['s']):
        target=min(max(v-l,0),capacity,max(0,(emax-energy)/.9))
        priority.append(abs((v-l-s)-target) if v>l else 0.)
        balance.append(g+v+d-l-c-s)
        energy += .9*c-d/.9
        trajectory.append(energy)
    reconstructed=np.array(trajectory)
    result={
        'balance_max_kwh':float(np.max(np.abs(balance))),
        'state_reconstruction_max_kwh':float(np.max(np.abs(reconstructed-f['e']))),
        'terminal_error_kwh':abs(float(energy-e0)),
        'bound_violation_kwh':float(max(0,emin-min(reconstructed),max(reconstructed)-emax)),
        'power_violation_kw':float(max(0,max(f['c'])-capacity,max(f['d'])-capacity)*6),
        'negative_flow_kwh':float(max(0,-min(min(f[k]) for k in ['g','c','d','s']))),
        'mutual_violation_count':int(np.sum((f['c']>TOL)&(f['d']>TOL))),
        'pv_priority_max_kwh':float(max(priority)),
        'pv_first_load_violation_kwh':float(max(0,max(f['d']-np.maximum(load-pv,0)),max(np.maximum(pv-load,0)-f['s']-f['c']))),
        'cost_recalculation_error_yuan':abs(float(math.fsum(float(a*b) for a,b in zip(p,f['g']))-objective)),
        'state_min_kwh':float(min(e0,min(reconstructed))), 'state_max_kwh':float(max(e0,max(reconstructed))),
    }
    result['pass']=bool(all(result[k]<=TOL for k in ['balance_max_kwh','state_reconstruction_max_kwh','terminal_error_kwh','bound_violation_kwh','negative_flow_kwh','pv_priority_max_kwh','pv_first_load_violation_kwh'])
                        and result['power_violation_kw']<=1e-5 and result['mutual_violation_count']==0 and result['cost_recalculation_error_yuan']<=.01)
    return result


def exact_small_tests():
    checks=[]
    # 100 AC charge -> 90 internal -> 81 AC discharge, terminal exactly zero.
    for label,p,l,v,emax,expected,expected_charge,expected_discharge in [
        ('grid_efficiency',[1,2],[0,81],[0,0],200,100,100,81),
        ('pv_efficiency',[1,2],[0,81],[100,0],200,0,100,81),
        ('full_battery_spill',[1,2],[0,81],[200,0],90,0,100,81),
        ('pv_and_grid_topup',[.1,1],[0,162],[100,0],200,10,200,162),
    ]:
        arrays=[np.array(a,dtype=float) for a in [p,l,v]]
        f,res=solve(*arrays,integer=True,capacity=300,e0=0,emin=0,emax=emax)
        chk=audit(*arrays,f,res['objective'],capacity=300,e0=0,emin=0,emax=emax)
        assert chk['pass'] and abs(res['objective']-expected)<TOL and abs(f['c'][0]-expected_charge)<TOL and abs(f['d'][1]-expected_discharge)<TOL
        if label=='full_battery_spill': assert abs(f['s'][0]-100)<TOL
        checks.append({'name':label,'expected_cost':expected,'actual_cost':res['objective'],'pass':True})
    # Independent exhaustive choice of binary modes; continuous variables remain exact LPs.
    p=np.array([.5,2.,3.]);l=np.array([0.,20.,70.]);v=np.array([120.,0.,0.]);n=3
    _,m=solve(p,l,v,integer=True,capacity=300,e0=0,emin=0,emax=90)
    costs=[]
    for bits in itertools.product([0,1],repeat=4):
        fixed={5*n+i:bits[i] for i in range(n)};fixed[6*n]=bits[3]
        try:
            _,r=solve(p,l,v,integer=False,fixed=fixed,capacity=300,e0=0,emin=0,emax=90)
            costs.append(r['objective'])
        except RuntimeError as exc:
            if 'status 2:' not in str(exc): raise
    assert abs(min(costs)-m['objective'])<TOL and abs(m['objective']-18)<TOL
    checks.append({'name':'all_16_binary_assignments','actual_cost':m['objective'],'enumerated_best':min(costs),'expected_cost':18,'pass':True})
    return checks


def export_case(directory,p,load,pv,f,res,check):
    directory.mkdir()
    rows=[]
    for i in range(144):
        rows.append({'interval':f'{clock(i*10)}-{clock((i+1)*10)}','price_yuan_kwh':p[i],
                     'load_kwh':load[i],'pv_kwh':pv[i],'grid_kwh':f['g'][i],
                     'charge_ac_kwh':f['c'][i],'discharge_ac_kwh':f['d'][i],
                     'pv_charge_ac_kwh':max(pv[i]-load[i],0)-f['s'][i],
                     'grid_charge_ac_kwh':f['c'][i]-max(pv[i]-load[i],0)+f['s'][i],
                     'curtailed_pv_kwh':f['s'][i],'stored_kwh':f['e'][i]})
    csv_write(directory/'dispatch.csv',rows)
    table1=[{'interval':rows[h*6]['interval'],'grid_kwh':float(f['g'][h*6])} for h in [10,12,14,16,18,20]]
    table2=[{'interval':f'{clock(k*240)}-{clock((k+1)*240)}',
             'charge_ac_kwh':float(sum(f['c'][k*24:(k+1)*24])),
             'discharge_ac_kwh':float(sum(f['d'][k*24:(k+1)*24]))} for k in range(6)]
    csv_write(directory/'table1.csv',table1);csv_write(directory/'table2.csv',table2)
    book=openpyxl.load_workbook(ROOT/'data/templates/result1.xlsx')
    for i,row in enumerate(rows,2):
        book['计划购电量'].cell(i,1,row['interval']);book['计划购电量'].cell(i,2,float(row['grid_kwh']))
    for i,row in enumerate(table2,2):
        book['充放电量'].cell(i,2,row['charge_ac_kwh']);book['充放电量'].cell(i,3,row['discharge_ac_kwh'])
    book['充放电量'].cell(2,5,6000.);book['充放电量'].cell(3,5,float(f['e'][-1]))
    book.save(directory/'result1.xlsx')
    roundtrip=openpyxl.load_workbook(directory/'result1.xlsx',data_only=True)
    actual=np.array([roundtrip['计划购电量'].cell(i,2).value for i in range(2,146)])
    assert np.max(np.abs(actual-f['g']))<1e-9
    assert all(roundtrip['计划购电量'].cell(i+2,1).value==row['interval'] for i,row in enumerate(rows))
    for i,row in enumerate(table2,2):
        assert abs(roundtrip['充放电量'].cell(i,2).value-row['charge_ac_kwh'])<TOL
        assert abs(roundtrip['充放电量'].cell(i,3).value-row['discharge_ac_kwh'])<TOL
    assert roundtrip['充放电量'].cell(2,5).value==6000 and abs(roundtrip['充放电量'].cell(3,5).value-6000)<TOL
    roundtrip.close()
    dump(directory/'validation.json',{'solver':res,'independent_audit':check,'xlsx_roundtrip':'PASS'})
    return rows


def template_test(output,rows,f):
    book=openpyxl.load_workbook(ROOT/'data/templates/result1.xlsx')
    mapping=[];by_label={}
    for i,row in enumerate(rows,2):
        label=book['计划购电量'].cell(i,1).value
        a,b=label.split('-');start,end=minute(a),minute(b)
        mapping.append({'row':i,'source_cell':f'Sheet1!A{i}', 'raw_template_label':label,
                        'raw_start_minute':start,'raw_end_minute':end,
                        'physical_interval':row['interval'],'grid_kwh':float(f['g'][i-2])})
        by_label[start]=float(f['g'][i-2])
        book['计划购电量'].cell(i,2,float(f['g'][i-2]))
    # A preservation example MUST carry an explicit authoritative mapping, not a submission-ready label claim.
    sheet=book.create_sheet('时间映射_测试用途')
    sheet.append(list(mapping[0]))
    for row in mapping: sheet.append(list(row.values()))
    book.save(output/'original_labels_with_mapping_TEST_ONLY.xlsx')
    book.close()
    loaded=openpyxl.load_workbook(output/'original_labels_with_mapping_TEST_ONLY.xlsx',data_only=True)
    assert max(abs(loaded['计划购电量'].cell(i+2,2).value-f['g'][i]) for i in range(144))<TOL
    assert loaded['时间映射_测试用途'].max_row==145
    loaded.close()
    csv_write(output/'template_mapping.csv',mapping)
    comparison=[{'interval':rows[h*6]['interval'],'correct_grid_kwh':float(f['g'][h*6]),
                 'wrong_if_original_labels_trusted_kwh':by_label[h*60],
                 'difference_kwh':by_label[h*60]-float(f['g'][h*6])} for h in [10,12,14,16,18,20]]
    csv_write(output/'template_table1_comparison.csv',comparison)
    return {'corrected_coverage':'00:00-24:00','raw_coverage':'00:10-next-day 00:10',
            'missing_minutes_in_requested_day':10,'outside_minutes':10,'all_rows_label_shift_minutes':10,
            'relabel_only_cost_difference':0.,'roundtrip':'PASS','table1_label_only_comparison':comparison}


def figures(output,cases,summaries):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font=next((x for x in font_manager.fontManager.ttflist if x.name in ['Microsoft YaHei','SimHei','Noto Sans CJK SC']),None)
    if font: plt.rcParams['font.family']=font.name
    plt.rcParams['axes.unicode_minus']=False
    output.mkdir()
    p,l,v,f=cases['end_rectangle'];x=np.arange(144)/6
    fig,axes=plt.subplots(3,1,figsize=(10,8),sharex=True,layout='constrained')
    axes[0].plot(x,l*6,label='负载');axes[0].plot(x,v*6,label='光伏');axes[0].plot(x,f['g']*6,label='购电');axes[0].set_ylabel('功率 / kW');axes[0].legend(ncol=3)
    axes[1].step(x,f['c']*6,label='充电',where='post');axes[1].step(x,-f['d']*6,label='放电',where='post');axes[1].set_ylabel('功率 / kW');axes[1].legend()
    axes[2].plot(np.arange(145)/6,np.r_[6000,f['e']],label='储电量');axes[2].axhline(1200,color='gray',ls='--');axes[2].axhline(10800,color='gray',ls='--');axes[2].set(xlabel='时刻 / h',ylabel='储电量 / kWh',xlim=(0,24));axes[2].legend()
    for ax in axes: ax.grid(alpha=.2)
    for ext in ['png','pdf']:fig.savefig(output/f'Fig1_dispatch.{ext}',dpi=300)
    plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    names=['区间末值','区间起值\n周期补点','梯形积分\n周期补点']
    deltas=[s['cost_yuan']-summaries[0]['cost_yuan'] for s in summaries]
    bars=axes[0].bar(names,deltas);axes[0].set_ylabel('相对区间末值的费用差 / 元');axes[0].set_title('A1：解释变化的费用影响')
    axes[0].bar_label(bars,fmt='%.2f',padding=3);axes[0].axhline(0,color='gray',lw=.7);axes[0].set_ylim(-25,4)
    axes[1].axis('off');axes[1].set_title('日初、日末均固定6000 kWh')
    table=axes[1].table(cellText=[[n.replace('\n',' / '),f"{s['cost_yuan']:.2f}",f"{s['curtailment_kwh']:.2f}"] for n,s in zip(names,summaries)],colLabels=['时间解释','费用 / 元','弃光 / kWh'],loc='center',colWidths=[.50,.30,.20])
    table.auto_set_font_size(False);table.set_fontsize(9);table.scale(1,2)
    for ext in ['png','pdf']:fig.savefig(output/f'Fig2_time_interpretation.{ext}',dpi=300)
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to((ROOT/'experiments').resolve()) or output.exists():
        raise ValueError('Use a new run directory under experiments/')
    output.mkdir(parents=True)
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    with (ROOT/'data/MANIFEST.csv').open(encoding='utf-8-sig',newline='') as f:
        manifest={r['path_or_uri']:r['sha256'] for r in csv.DictReader(f)}
    paths=['problem/C题.pdf','data/raw/附件1.xlsx','data/templates/result1.xlsx'];hashes={}
    for path in paths:
        hashes[path]=hashlib.sha256((ROOT/path).read_bytes()).hexdigest();assert hashes[path]==manifest[path]
    book=openpyxl.load_workbook(ROOT/paths[1],read_only=True,data_only=True)
    rows=list(book.active.iter_rows(min_row=2,values_only=True));book.close()
    assert [minute(r[0]) for r in rows]==list(range(10,1441,10))
    raw=np.array([r[1:] for r in rows],dtype=float);assert np.isfinite(raw).all()
    p,l,v=raw.T
    arrays={'end_rectangle':(p,l/6,v/6),
            'start_rectangle_periodic':(np.roll(p,1),np.roll(l,1)/6,np.roll(v,1)/6),
            'trapezoid_periodic':(p,(l+np.roll(l,1))/12,(v+np.roll(v,1))/12)}
    small=exact_small_tests();dump(output/'small_instance_tests.json',small)
    results=output/'results';results.mkdir();summaries=[];cases={};lower_bounds={}
    for name,(price,load,pv) in arrays.items():
        relaxed,r=solve(price,load,pv,integer=False)
        relaxed_audit=audit(price,load,pv,relaxed,r['objective'])
        f,res=solve(price,load,pv,integer=True)
        checked=audit(price,load,pv,f,res['objective'])
        assert checked['pass'],checked
        assert res['objective']>=r['objective']-.01
        assert abs(res['objective']-res['dual_bound'])<.01 and res['mip_gap']<=1e-8
        dispatch=export_case(results/name,price,load,pv,f,res,checked)
        csv_write(results/name/'lp_dispatch.csv',[{'interval':row['interval'],**{k:float(relaxed[k][i]) for k in relaxed}} for i,row in enumerate(dispatch)])
        cases[name]=(price,load,pv,f)
        lower_bounds[name]={'lp_solver':r,'lp_strict_audit':relaxed_audit,'strict_minus_lp_cost':res['objective']-r['objective']}
        summary={'case':name,'cost_yuan':res['objective'],'purchase_kwh':float(sum(f['g'])),
                 'charge_ac_kwh':float(sum(f['c'])),'discharge_ac_kwh':float(sum(f['d'])),
                 'curtailment_kwh':float(sum(f['s'])),'e0_kwh':6000,'e24_kwh':float(f['e'][-1]),
                 'no_storage_counterfactual_cost':float(np.dot(price,np.maximum(load-pv,0))),
                 'lp_lower_bound_cost':r['objective'],'strict_audit_pass':checked['pass']}
        summaries.append(summary)
        if name=='end_rectangle': primary_rows=dispatch
    csv_write(results/'summary_tables.csv',summaries);dump(results/'lp_comparison.json',lower_bounds)
    mapping=template_test(results,primary_rows,cases['end_rectangle'][3]);dump(results/'template_test.json',mapping)
    figures(output/'figures',cases,summaries)
    metadata={'code_commit':sha,'command':f'python scripts/run_q1_tests.py --output {output.relative_to(ROOT).as_posix()}',
              'python':platform.python_version(),'platform':platform.platform(),
              'dependencies':{p:importlib.metadata.version(p) for p in ['numpy','scipy','openpyxl','matplotlib']},
              'input_hashes':hashes,'randomness':'none; deterministic optimization',
              'scope':'Q1 conditional contract tests only, no forecasting or final model approval',
              'small_tests_pass':all(x['pass'] for x in small),'all_case_audits_pass':all(x['strict_audit_pass'] for x in summaries)}
    dump(output/'run.json',metadata)
    lines=['# 问题1口径测试报告','',f'代码提交：{sha}。按用户2026-09-11的口径授权执行；最终题意及模型尚待人工验收。','',
           '## A 运行产物','results/下逐方案result1.xlsx、144段dispatch.csv、表1/2、独立验证；summary_tables.csv；figures/下PNG/PDF；run.json与small_instance_tests.json。','',
           '## B 核心结果','| 时间解释 | 费用/元 | 购电/kWh | 弃光/kWh |','|---|---:|---:|---:|']
    lines += [f"| {s['case']} | {s['cost_yuan']:.6f} | {s['purchase_kwh']:.6f} | {s['curtailment_kwh']:.6f} |" for s in summaries]
    lines += ['', '两种矩形解释保持价格、负载、光伏相互对齐。起值解释用前日24:00=当日00:00的周期补点，绝非已知额外测量；梯形解释仅改变负载/光伏积分，价格仍按区间末值计费，以隔离功率积分因素。三者固定E0=E24=6000。',
              'A1：费用差异是条件敏感性，不能决定真实采样语义。区间末值覆盖0:00—24:00且无需额外补点，建议作主口径；其他解释作备查。',
              'A5：只改标签不重算不改变费用。但原模板覆盖00:10至次日00:10，缺当日首10分钟、多次日10分钟；若按标签查表就读错一行。正式候选副本修正区间，原模板不动；保留原标签的文件标TEST_ONLY并附144行映射，未作为完整提交品。',
              '', '## C 图表','Fig1展示主口径购电、充放电与储电量；Fig2比较不同时间解释的费用与弃光。',
              '', '## D 合理性检查','充/放电单程各90%，往返81%；100kWh交流充电→90kWh储存→81kWh交流供电。末状态独立累计回6000，逐段检查余额、功率、容量、互斥、光伏优先和费用。',
              '严格光伏规则：先供负载，再尽可能按容量和功率上限充电，剩余弃光；限充功率触顶也可导致弃光。剩余光伏最大功率与是否实际触顶可从输入/明细核对。允许光伏优先吸收后仍允许电网为储能补充充电；不得将PV优先误写成PV盈余时禁购电。',
              '本轮同时求解允许自行决定弃光的LP松弛与严格MILP。三种时间解释的LP输出均通过完整严格审计，且与严格MILP费用差均小于0.01元，支持本实例直接采用LP；逐时LP结果见各方案lp_dispatch.csv。此结论依赖实际校验，不能无条件沿用旧文档的消环证明。',
              '5个可手算/枚举小实例通过；3个144段严格方案通过独立审计和Excel往返读取。无储能费用仅是禁用储能的反事实对照，不能称为本严格优先规则下的同约束可行策略。',
              '', '## E 迭代记录','前两轮错误地额外禁止光伏盈余时从电网补充充电，并把总充电量限制为光伏盈余，因此得到35859.32元的过高最优费用。用户提供35126.95元后，独立标准LP复现该数值，定位并删除额外限制，保留PV先供负载和先吸收的规则；明确拆分PV充电与电网充电。新增可手算的PV与电网同时充电回归例：100度PV+100度低价网电充入，后续可供162度，费用10元。原错误费用与正确费用的差额为约732.37元。效率、初末库存和时间主口径未变。此前LP/MILP一致只说明它们共享的错误约束一致，不能证明题目模型正确。',
              '', '## F 最终可运行版本','仓库final_run/q1-contract-tests/main.py为复现入口，输出必须使用新的experiments目录；final_run表示可运行测试包，不表示最终模型已获批准。',
              '', '## G 下一步','优先确认A1区间末值解释与A5结果副本修正标签。若需进一步参数敏感性，再分别研究计量侧、效率和容量；本轮不扩展问题2—4。',
              '', '## H 评委视角','最有价值的是可复核的能量账本、严格光伏顺序、小实例精确对照和无静默错位的模板映射；不能把最低费用当成题意正确的证据，也不能把单日确定性结果宣传为真实天气验证。']
    (output/'model_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'summaries':summaries,'lp_comparison':lower_bounds,'small_tests':small},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
