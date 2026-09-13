"""Restyle two audited Q2 figures, retaining their original data and filenames."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter
import numpy as np

COLORS=['#A1DEE1','#76CDE7','#51B8F1','#2469A9']

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):
    with path.open(encoding='utf-8-sig',newline='') as stream: return list(csv.DictReader(stream))
def style(ax,grid='y'):
    ax.spines[['top','right']].set_visible(False)
    for side in ['left','bottom']:
        ax.spines[side].set_color('#262A2D');ax.spines[side].set_linewidth(.8)
    ax.tick_params(length=3,width=.8,color='#262A2D')
    ax.set_axisbelow(True);ax.grid(axis=grid,color='#EBEFF2',linewidth=.65)

def main(run):
    cfg_path=run/'run.json';periods_path=run/'periods.csv';dispatch_path=run/'operational_dispatch.csv'
    figures=run/'figures';figures.mkdir(exist_ok=True)
    sources=[cfg_path,periods_path,dispatch_path]
    before={path.name:sha(path) for path in sources}
    other_before={f.name:sha(f) for f in figures.glob('Fig1_monthly_cost.*')}
    assert json.loads((run/'independent_audit.json').read_text(encoding='utf-8'))['pass']
    meta=json.loads(cfg_path.read_text(encoding='utf-8'));cfg=meta['config']
    periods=read(periods_path);quantiles=cfg['quantiles'];q=float(meta['selected_quantile'])
    values={}
    for period in ['selection','validation','holdout']:
        records=[next(r for r in periods if r['strategy']==f'q{quantile:g}' and r['period']==period) for quantile in quantiles]
        values[period]=[float(r['inventory_adjusted_mean'])/int(r['days'])/1e4 for r in records]
    # The plotted selection uses the historical selection period only.
    assert quantiles[int(np.argmin(values['selection']))]==q
    selected={date:[] for date in cfg['paper_dates']}
    with dispatch_path.open(encoding='utf-8-sig',newline='') as stream:
        for row in csv.DictReader(stream):
            if row['date'] in selected: selected[row['date']].append(row)
    trajectories={}
    for date,rows in selected.items():
        rows.sort(key=lambda row:int(row['slot']))
        assert [int(row['slot']) for row in rows]==list(range(144))
        assert all(abs(float(a['e_end'])-float(b['e_start']))<1e-7 for a,b in zip(rows,rows[1:]))
        energy=[float(rows[0]['e_start'])]+[float(row['e_end']) for row in rows]
        assert min(energy)>=cfg['energy_min']-1e-7 and max(energy)<=cfg['energy_max']+1e-7
        trajectories[date]=energy
    plt.rcParams.update({'font.family':['Times New Roman','Microsoft YaHei'],
        'font.size':10.5,'axes.labelsize':11,'axes.titlesize':12.5,
        'axes.titleweight':'bold','axes.titlepad':12,'axes.unicode_minus':False,
        'pdf.fonttype':42,'ps.fonttype':42,'text.color':'#24272A',
        'axes.labelcolor':'#24272A','xtick.color':'#24272A','ytick.color':'#24272A',
        'figure.facecolor':'white','axes.facecolor':'white'})
    def save(fig,name):
        fig.savefig(figures/f'{name}.png',dpi=400,bbox_inches='tight',facecolor='white')
        fig.savefig(figures/f'{name}.pdf',bbox_inches='tight',facecolor='white')
        plt.close(fig)

    fig,ax=plt.subplots(figsize=(7.2,4),layout='constrained');style(ax)
    specs=[('selection','2—4月选参',COLORS[0],'^'),
           ('validation','5—8月验证',COLORS[1],'s'),
           ('holdout','9—12月留出',COLORS[3],'o')]
    for key,label,color,marker in specs:
        ax.plot(quantiles,values[key],label=label,color=color,marker=marker,
            linewidth=2,markersize=5.8,markeredgecolor='white',markeredgewidth=.7,zorder=3)
    ax.axvline(q,ymax=.88,color='#AEB8C0',linewidth=.85,linestyle=(0,(4,4)),zorder=1)
    index=quantiles.index(q);score=values['selection'][index]
    ax.annotate(f'选定分位数 q = {q:.2f}',xy=(q,score),xytext=(.735,4.61),
        fontsize=9.7,color=COLORS[3],
        arrowprops={'arrowstyle':'->','color':COLORS[3],'lw':.8})
    ax.set_title('不同分位数下的费用表现')
    ax.set_xlabel('固定分位数');ax.set_ylabel('库存调整日均费用（万元）')
    ax.set_xticks(quantiles);ax.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
    ax.set_xlim(.585,.965);ax.set_ylim(4.30,5.52)
    ax.legend(loc='upper center',frameon=False,fontsize=9.5,handlelength=2.8,
        ncol=3,columnspacing=1.5,borderaxespad=.8)
    save(fig,'Fig2_quantile_comparison')

    fig,axes=plt.subplots(2,2,figsize=(7.2,5.3),sharex=True,sharey=True)
    fig.subplots_adjust(left=.115,right=.985,bottom=.11,top=.835,wspace=.09,hspace=.34)
    bound=None
    for i,(ax,date) in enumerate(zip(axes.flat,cfg['paper_dates'])):
        style(ax)
        ax.plot(np.arange(145)/6,trajectories[date],color=COLORS[i],linewidth=1.9,
            marker=['^','s','o','D'][i],markersize=4.3,markevery=24,
            markeredgecolor='white',markeredgewidth=.6,zorder=3)
        for value in [cfg['energy_min'],cfg['energy_max']]:
            bound=ax.axhline(value,color='#8E9CA7',linestyle=(0,(4,3)),linewidth=.8,zorder=2)
        ax.set_title(f'({chr(97+i)})  {date}',fontsize=10.5,pad=10)
        ax.set_xlim(0,24);ax.set_ylim(500,11500)
        ax.set_xticks(range(0,25,4));ax.set_yticks([1200,6000,10800])
        ax.tick_params(labelsize=9)
    fig.suptitle('指定日期的实际储电量轨迹',fontsize=12.5,fontweight='bold',y=.99)
    fig.supxlabel('时刻（h）',fontsize=11,y=.02);fig.supylabel('内部储电量（kWh）',fontsize=11,x=.005)
    fig.legend([bound],['储能上下限：1,200 / 10,800 kWh'],loc='upper center',bbox_to_anchor=(.5,.94),
        frameon=False,fontsize=9,handlelength=3)
    save(fig,'Fig3_paper_day_storage')
    assert before=={path.name:sha(path) for path in sources}
    assert other_before=={f.name:sha(f) for f in figures.glob('Fig1_monthly_cost.*')}
    result={'pass':True,'source_sha256':before,'unmodified_other_figure_sha256':other_before,
        'quantiles':quantiles,'period_daily_cost_wan':values,'selected_quantile':q,
        'paper_dates':cfg['paper_dates'],'storage_kwh_145_points':trajectories,
        'energy_bounds':[cfg['energy_min'],cfg['energy_max']],
        'palette':COLORS,'files':['Fig2_quantile_comparison.png','Fig2_quantile_comparison.pdf',
            'Fig3_paper_day_storage.png','Fig3_paper_day_storage.pdf'],
        'publication_status':'local review; user confirmation required before GitHub push'}
    (figures/'q2_style_review_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Redrawn two PNG/PDF figures. Original data and Fig1 hashes unchanged. Local review only.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('experiment',type=Path)
    args=parser.parse_args();main(args.experiment.resolve())
