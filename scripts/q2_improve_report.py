from pathlib import Path
import sys,json,csv,gzip,importlib.util
from collections import defaultdict
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1]
def loadmod(path):
    s=importlib.util.spec_from_file_location('q2_report_reuse',path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def main(run):
    report=loadmod(ROOT/'final_run/q2/report.py')
    read=report.read
    meta=json.loads((run/'run.json').read_text(encoding='utf-8'));cfg=meta['config']
    chosen=json.loads((run/'selection.json').read_text(encoding='utf-8'))['selected_by_feb_apr']
    specs=json.loads((run/'specs.json').read_text(encoding='utf-8'));spec=next(s for s in specs if s['name']==chosen)
    with gzip.open(run/(chosen+'_dispatch.csv.gz'),'rt',encoding='utf-8',newline='') as f: rows=list(csv.DictReader(f))
    sheets,_=report.build_workbook_tables(run,cfg,rows,spec['q'],initial_quantile=spec['q'])
    # Regression: refactored helper must reproduce the original seven-sheet payload.
    old=ROOT/'experiments/q2-full-20260911-01'
    checkdir=run/'table_regression';checkdir.mkdir(exist_ok=True)
    payload,_=report.build_workbook_tables(checkdir,cfg,read(old/'operational_dispatch.csv'),.8)
    expected=json.loads((old/'workbook_data.json').read_text(encoding='utf-8'))
    assert payload.keys()==expected.keys()
    maxerr=0.
    for key in payload:
        assert payload[key]["headers"]==expected[key]["headers"] and len(payload[key]["rows"])==len(expected[key]["rows"])
        for a,b in zip(payload[key]["rows"],expected[key]["rows"]):
            assert len(a)==len(b)
            for x,y in zip(a,b):
                if isinstance(x,(int,float)) and isinstance(y,(int,float)):maxerr=max(maxerr,abs(x-y))
                else:assert x==y
    assert maxerr<1e-6
    (run/'table_regression.json').write_text(json.dumps(dict(pass_=True,original_sheets=len(payload),max_numeric_error=maxerr))+'\n')
    total=read(run/'summary.csv');lookup={r['strategy']:r for r in total};b=lookup['control'];best=lookup[chosen]
    f=lambda r,k:float(r[k])
    descriptions={
        'control':'原0.8方案','mean3':'3日均值','mean14':'14日均值','weighted':'近期指数加权',
        'weekday':'同星期负载预测','residual14':'14天误差窗口','residual56':'56天误差窗口',
        'target1200':'日末目标1200','target3600':'日末目标3600','target8400':'日末目标8400',
        'horizon48':'48小时重复预测规划','planned_reserve':'按计划库存保留',
        'combined':'组合：同星期+14天误差+目标1200','combined48':'组合+48小时规划'}
    for s in specs:
        if s['name'].startswith('q'):descriptions[s['name']]='分位数'+str(s['q'])
    periods=read(run/'periods.csv')
    header=['方案','总费用/元','较原方案节省/元','紧急电量/kWh','未用计划电/kWh','期末库存/kWh']
    vals=[[descriptions[r['strategy']],f(r,'total_cost'),f(b,'total_cost')-f(r,'total_cost'),
           f(r,'emergency_kwh'),f(r,'paid_grid_spill_kwh'),f(r,'e_end')] for r in total]
    lines=['# 第二问优化实验结果','',
        '本轮为已看过全年结果后的回顾性开发比较，不是新盲测。18个候选均运行2—12月334天；共享同一1月热启动。原15971772.85元方案在本环境复现至分钱以内。',
        '', '## 使用与复用',
        '复用scripts/q2_core.py的LP/MILP、余量和执行器，复用原11项小实例，复用final_run/q2/report.py的七张表生成与workbook.mjs导出。新代码只增加预测候选、参数比较、48小时规划及计划库存保留策略，未引入DP。',
        '', '## 全部候选',report.md(header,vals),'',
        '## 选择与结论',
        '组合中的预测方式、误差窗口、分位数及日末目标，仅依据2—4月库存调整费用选取。随后比较预先规定的组合和48小时组合；全部候选也只按2—4月分数选择。固定组合从2月运行的全年结果是事后候选回测，不冒充当时已经部署的策略。',
        f'选中{chosen}：{json.dumps(spec,ensure_ascii=False)}。',
        f'现金费用由{f(b,"total_cost"):,.2f}元降至{f(best,"total_cost"):,.2f}元，节省{f(b,"total_cost")-f(best,"total_cost"):,.2f}元（{1-f(best,"total_cost")/f(b,"total_cost"):.2%}）。',
        f'计划费用减少{f(b,"plan_cost")-f(best,"plan_cost"):,.2f}元，紧急费用减少{f(b,"emergency_cost")-f(best,"emergency_cost"):,.2f}元；未用计划电减少{f(b,"paid_grid_spill_kwh")-f(best,"paid_grid_spill_kwh"):,.2f}kWh。',
        f'原方案期末{f(b,"e_end"):,.2f}kWh，候选期末{f(best,"e_end"):,.2f}kWh，不能忽略差异。按相同均价库存修正，仍节省{f(b,"adjusted_cost")-f(best,"adjusted_cost"):,.2f}元；这只是统一价值近似，不是现金收入或严格同末端约束优化。',
        '主要改善来自同星期负载预测；余量仍取0.8。单独缩短误差窗口不保证全年获益，组合改善不能全部归因于该窗口。48小时重复预测和计划库存保留在本轮不如对应单日方案，不予推荐。',
        '', '## 分期比较（均为开发性复核）']
    pp=[]
    for period in ['development','later_1','later_2']:
        base=next(r for r in periods if r['strategy']=='control' and r['period']==period)
        win=next(r for r in periods if r['strategy']==chosen and r['period']==period)
        pp.append([period,f(base,'total_cost'),f(win,'total_cost'),100*(1-f(win,'total_cost')/f(base,'total_cost'))])
    lines += [report.md(['区间：2—4/5—8/9—12月','原方案费用','候选费用','降幅/%'],pp),'',
        '## 独立审计与边界',
        'independent_audit.json逐候选复算历史预测、分位余量、完整规划约束、实际账本、费用和选择分数；原始365天来源与已核验快照完全一致。info检查证明改变未来数据不影响当前预测。完整候选计划和实际动作各保存在同名gzip文件中。',
        '日末1200是优化器的预测终端目标，不是实际每天强制放空；真实库存仍连续且不得低于1200。48小时候选只是重复当时估计的日曲线，不能据其失败否定更准确预测下的滚动优化。计划库存保留不是DP，失败也不能证明所有备用策略无效。',
        '本轮候选Excel单独交付，不覆盖T-006正式result2.xlsx。该候选尚待用户审查，不自动取代原模型。','']
    (run/'model_report.md').write_text('\n'.join(lines),encoding='utf-8')
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'pdf.fonttype':42})
    fig,ax=plt.subplots(figsize=(11,8))
    labels=[descriptions[r['strategy']] for r in total]
    y=list(range(len(total)));cost=[f(r,'total_cost')/1e4 for r in total]
    ax.barh(y,cost,color=['#B86E49' if r['strategy']==chosen else '#477A8B' for r in total])
    ax.set_yticks(y,labels,fontsize=9);ax.invert_yaxis();ax.set_xlabel('334天总费用 / 万元')
    ax.axvline(f(b,'total_cost')/1e4,color='gray',linestyle='--',linewidth=1)
    for yy,c in zip(y,cost):ax.text(c+4,yy,f'{c:.2f}',va='center',fontsize=8)
    ax.set_xlim(0,max(cost)*1.10);fig.tight_layout()
    fig.savefig(run/'cost_comparison.png',dpi=160);fig.savefig(run/'cost_comparison.pdf');plt.close(fig)
    print(json.dumps(dict(chosen=chosen,cash_saving=f(b,'total_cost')-f(best,'total_cost'),
          adjusted_saving=f(b,'adjusted_cost')-f(best,'adjusted_cost'),periods=pp),ensure_ascii=False,indent=2))
if __name__=='__main__':main(ROOT/sys.argv[1])
