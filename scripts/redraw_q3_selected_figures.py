"""Redraw audited Q3 results with direction three and cyan/blue styling.

No optimization or result CSV is changed. The two controls are benchmarks;
rolling_point, rolling_margin and rolling_point_step are not plotted.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator, StrMethodFormatter
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
NAMES = ['no_update', 'state_only', 'rolling_scenario']
LABELS = ['仅0时预报', '状态反馈对照', '连续误差场景模型']
COLORS = ['#A1DEE1', '#76CDE7', '#2469A9']
FIELDS = ['ordinary_cost', 'adjustment_cost', 'emergency_cost']

def read(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))

def style(ax, grid='x'):
    ax.spines[['top','right']].set_visible(False)
    for side in ['left','bottom']:
        ax.spines[side].set_linewidth(.8)
        ax.spines[side].set_color('#262A2D')
    ax.tick_params(length=3, width=.8, color='#262A2D')
    ax.set_axisbelow(True)
    ax.grid(axis=grid, color='#EBEFF2', linewidth=.65)

def main(run):
    summary_path=run/'results/summary_tables.csv'
    daily_path=run/'results/daily_summary.csv'
    before={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [summary_path,daily_path]}
    if not json.loads((run/'audit_summary.json').read_text(encoding='utf-8'))['pass_']:
        raise AssertionError('Requires independently audited model results')
    lookup={r['strategy']:r for r in read(summary_path)}
    daily=read(daily_path)
    months=sorted({r['date'][:7] for r in daily})
    assert len(months)==11
    monthly={name:[math.fsum(float(r['total_cost']) for r in daily
        if r['strategy']==name and r['date'].startswith(month))/1e4 for month in months]
        for name in NAMES}
    for name in NAMES:
        chosen=[r for r in daily if r['strategy']==name]
        assert len(chosen)==334 and len({r['date'] for r in chosen})==334
        for field in FIELDS+['total_cost']:
            assert abs(math.fsum(float(r[field]) for r in chosen)-float(lookup[name][field]))<1e-6
        assert abs(math.fsum(float(lookup[name][field]) for field in FIELDS)-float(lookup[name]['total_cost']))<1e-6
    plt.rcParams.update({'font.family':['Times New Roman','Microsoft YaHei'],
        'font.size':10.5,'axes.labelsize':11,'axes.titlesize':12.5,
        'axes.titleweight':'bold','axes.titlepad':12,
        'axes.unicode_minus':False,'pdf.fonttype':42,'ps.fonttype':42,
        'text.color':'#24272A','axes.labelcolor':'#24272A',
        'xtick.color':'#24272A','ytick.color':'#24272A',
        'figure.facecolor':'white','axes.facecolor':'white'})
    figures=run/'figures'; figures.mkdir(exist_ok=True)
    def save(fig,name):
        fig.savefig(figures/f'{name}.png',dpi=400,bbox_inches='tight',facecolor='white')
        fig.savefig(figures/f'{name}.pdf',bbox_inches='tight',facecolor='white')
        plt.close(fig)

    costs=np.array([float(lookup[name]['total_cost'])/1e4 for name in NAMES])
    fig,ax=plt.subplots(figsize=(7.2,3.65),layout='constrained')
    style(ax)
    ax.barh(np.arange(3),costs,height=.44,color=COLORS,edgecolor='white',linewidth=.7)
    ax.set_yticks(np.arange(3),LABELS)
    ax.set_xlim(0,1900); ax.set_ylim(2.7,-.6)
    ax.xaxis.set_major_locator(MultipleLocator(400))
    ax.set_xlabel('334天实际总费用（万元）')
    ax.set_title('全年购电费用比较')
    for i,value in enumerate(costs):
        ax.text(value+22,i,f'{value:,.2f}',va='center',fontsize=11,
                fontweight='bold' if i==2 else 'normal',color=COLORS[2] if i==2 else '#24272A')
    reduction=100*(1-costs[2]/costs[0])
    ax.text(.98,.03,f'较仅0时预报降低 {reduction:.2f}%',transform=ax.transAxes,
        ha='right',va='bottom',fontsize=10,color=COLORS[2])
    save(fig,'Fig1_Q3_TotalCost')

    selected=lookup['rolling_scenario']
    components=np.array([float(selected[field])/1e4 for field in FIELDS])
    total=float(selected['total_cost'])/1e4
    fig,ax=plt.subplots(figsize=(7.2,3.65),layout='constrained')
    style(ax)
    ax.barh(np.arange(3),components,height=.44,color=[COLORS[2],COLORS[1],COLORS[0]],
        edgecolor='white',linewidth=.7)
    ax.set_yticks(np.arange(3),['普通购电费','逐次调整费','紧急购电费'])
    ax.set_xlim(0,1660);ax.set_ylim(2.6,-.6)
    ax.xaxis.set_major_locator(MultipleLocator(400))
    ax.set_xlabel('费用（万元）');ax.set_title('全年购电费用构成')
    for i,value in enumerate(components):
        ax.text(value+22,i,f'{value:,.2f}  ·  {100*value/total:.2f}%',va='center',fontsize=10.5)
    ax.text(.98,.03,f'合计 {total:,.2f} 万元',transform=ax.transAxes,
        ha='right',va='bottom',fontweight='bold',color=COLORS[2])
    save(fig,'Fig2_Q3_CostBreakdown')

    fig,ax=plt.subplots(figsize=(7.2,4),layout='constrained')
    style(ax,'y');x=np.arange(len(months))
    for name,label,color,marker in zip(NAMES,LABELS,COLORS,['^','s','o']):
        ax.plot(x,monthly[name],label=label,color=color,marker=marker,
            linewidth=2.1 if name=='rolling_scenario' else 1.65,
            markersize=5,markeredgecolor='white',markeredgewidth=.65,
            zorder=3 if name=='rolling_scenario' else 2)
    ax.set_xticks(x,[f'{int(month[5:])}月' for month in months]); ax.set_xlim(-.35,10.35)
    ax.set_ylim(80,265);ax.yaxis.set_major_locator(MultipleLocator(40))
    ax.yaxis.set_major_formatter(StrMethodFormatter('{x:.0f}'))
    ax.set_ylabel('月度实际费用（万元）');ax.set_xlabel('2025年')
    ax.set_title('月度购电费用比较')
    ax.legend(frameon=False,loc='upper left',fontsize=9.2,handlelength=2.8,
        labelspacing=.6,borderaxespad=.7)
    save(fig,'Fig3_Q3_MonthlyCost')

    after={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in [summary_path,daily_path]}
    assert before==after
    metadata={'pass':True,'selected_direction':'rolling_scenario',
        'benchmark_strategies':['no_update','state_only'],
        'excluded_strategies':['rolling_point','rolling_margin','rolling_point_step'],
        'palette':COLORS,'source_sha256':before,'days':334,
        'monthly_cost_wan':monthly,'selected_total_cost':float(selected['total_cost']),
        'selected_component_costs':{field:float(selected[field]) for field in FIELDS},
        'figures':[f'{name}.{ext}' for name in ['Fig1_Q3_TotalCost','Fig2_Q3_CostBreakdown','Fig3_Q3_MonthlyCost'] for ext in ['png','pdf']]}
    (figures/'figure_data_audit.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Redrawn 3 PNG + 3 PDF; direction three; result CSV hashes unchanged: {run}')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('experiment',type=Path)
    args=parser.parse_args();main(args.experiment.resolve())
