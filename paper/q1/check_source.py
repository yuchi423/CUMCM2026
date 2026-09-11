"""Static/source checks only. Does not compile TeX or verify PDF layout."""
from pathlib import Path
import csv
import hashlib
import json
import math
import re
from datetime import datetime

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUN = ROOT / 'experiments/q1-contract-tests-20260911-03'
errors = []

def require(condition, message):
    if not condition:
        errors.append(message)

def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))

tex = (HERE / 'question1.tex').read_text(encoding='utf-8')
# Strip TeX comments while respecting escaped percent signs.
clean = re.sub(r'(?<!\\)%[^\n]*', '', tex)
require('@@' not in clean, 'Unfilled template token')
require(not re.search(r'\\(?:input|include|includegraphics|bibliography)\s*[\[{]', clean),
        'Unexpected external TeX/data/image dependency')
require(not re.search(r'TODO|待填写|WriteGuide|showguide', clean), 'Draft placeholder remains')
level = 0
for match in re.finditer(r'(?<!\\)[{}]', clean):
    level += 1 if match.group() == '{' else -1
    require(level >= 0, 'Unexpected closing brace')
require(level == 0, 'Unbalanced braces')
stack = []
for match in re.finditer(r'\\(begin|end)\{([^}]+)\}', clean):
    kind, env = match.groups()
    if kind == 'begin':
        stack.append(env)
    else:
        require(bool(stack) and stack[-1] == env, 'Environment mismatch: ' + env)
        if stack:
            stack.pop()
require(not stack, 'Unclosed environments')
labels = re.findall(r'\\label\{([^}]+)\}', clean)
refs = re.findall(r'\\(?:eqref|ref)\{([^}]+)\}', clean)
require(len(labels) == len(set(labels)), 'Duplicate labels')
require(set(refs) <= set(labels), 'Unresolved cross references')
require(set(re.findall(r'\\cite\{([^}]+)\}', clean)) <=
        set(re.findall(r'\\bibitem\{([^}]+)\}', clean)), 'Unresolved citations')

summary = rows(RUN / 'results/summary_tables.csv')
main = summary[0]
dispatch = rows(RUN / 'results/end_rectangle/dispatch.csv')
table1 = rows(RUN / 'results/end_rectangle/table1.csv')
table2 = rows(RUN / 'results/end_rectangle/table2.csv')
independent = json.loads((RUN / 'independent_lp_check.json').read_text(encoding='utf-8'))
macros = dict(re.findall(r'\\newcommand\{\\(\w+)\}\{([^{}]+)\}', clean))
expected = {
    'Cost': float(main['cost_yuan']),
    'GridTotal': float(main['purchase_kwh']),
    'ChargeTotal': float(main['charge_ac_kwh']),
    'DischargeTotal': float(main['discharge_ac_kwh']),
    'LossTotal': float(main['charge_ac_kwh']) - float(main['discharge_ac_kwh']),
    'NoStorageCost': float(main['no_storage_counterfactual_cost']),
    'Saving': float(main['no_storage_counterfactual_cost']) - float(main['cost_yuan']),
    'PVGridTopup': independent['strict_grid_topup_during_pv_surplus_kwh'],
}
for name, number in expected.items():
    require(name in macros and abs(float(macros[name]) - number) <= 0.00000051,
            'Numeric macro mismatch: ' + name)
percent = 100 * expected['Saving'] / expected['NoStorageCost']
require(abs(float(macros['SavingPercent']) - percent) <= 0.000051, 'Saving percent mismatch')
for r in table1:
    snippet = r['interval'].replace('-', '--') + ' & ' + f"{float(r['grid_kwh']):.6f}"
    require(snippet in clean, 'Purchase table mismatch: ' + r['interval'])
for r in table2:
    snippet = ' & '.join([r['interval'].replace('-', '--')] +
                        [f"{float(r[k]):.6f}" for k in ['charge_ac_kwh','discharge_ac_kwh']])
    require(snippet in clean, 'Storage table mismatch: ' + r['interval'])
for r in summary:
    require(f"{float(r['cost_yuan']):.6f}" in clean, 'Sensitivity result missing')

match = re.search(r'\\pgfplotstableread\[col sep=space\]\{\n(.*?)\n\}\\DispatchData', clean, re.S)
require(match is not None, 'Embedded plot table missing')
plot = []
if match:
    plot = list(csv.DictReader(match[1].splitlines(), delimiter=' '))
    require(len(plot) == 145, 'Expected 145 time boundaries')
    for i, r in enumerate(plot):
        d = dispatch[min(i, 143)]
        targets = {'time':i/6, 'energy':6000 if i == 0 else float(dispatch[i-1]['stored_kwh']),
                   'discharge':-6*float(d['discharge_ac_kwh']), 'price':float(d['price_yuan_kwh'])}
        targets.update({k:6*float(d[src]) for k, src in
                        [('load','load_kwh'),('pv','pv_kwh'),('grid','grid_kwh'),('charge','charge_ac_kwh')]})
        for name, number in targets.items():
            require(abs(float(r[name]) - number) < 1e-7, f'Plot mismatch: row {i} / {name}')

cost = math.fsum(float(r['price_yuan_kwh'])*float(r['grid_kwh']) for r in dispatch)
energy = 6000.0
max_balance = 0.0
for r in dispatch:
    d = {k:float(v) for k,v in r.items() if k != 'interval'}
    energy += .9*d['charge_ac_kwh'] - d['discharge_ac_kwh']/.9
    max_balance=max(max_balance, abs(d['grid_kwh']+d['pv_kwh']+d['discharge_ac_kwh']-
                                     d['load_kwh']-d['charge_ac_kwh']-d['curtailed_pv_kwh']))
require(abs(cost-expected['Cost']) < .01, 'Cost reconstruction mismatch')
require(abs(energy-6000) < 1e-6 and max_balance < 1e-6, 'Energy reconstruction mismatch')

sources=json.loads((HERE/'sources.json').read_text(encoding='utf-8'))
for rel, digest in sources['source_files'].items():
    require(hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()==digest, 'Source hash mismatch: '+rel)
require(hashlib.sha256((HERE/'question1.tex').read_bytes()).hexdigest()==sources['tex_sha256'],
        'TeX changed since provenance recorded')

result = {
    'checked_at': datetime.now().astimezone().isoformat(timespec='seconds'),
    'status': 'FAIL' if errors else 'PASS',
    'errors': errors, 'tex_lines':len(tex.splitlines()), 'labels':len(labels),
    'references':len(refs), 'embedded_plot_rows':len(plot),
    'numeric_macros_checked':len(expected)+1,
    'purchase_table_rows':len(table1), 'storage_table_rows':len(table2),
    'recomputed_cost_yuan':cost, 'recomputed_final_energy_kwh':energy,
    'source_hashes_checked':len(sources['source_files']),
    'compiled':False, 'page_layout_verified':False,
    'limit':'Checks source structure, data and references; does not validate TeX package behavior or page layout.'
}
(HERE/'static_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(bool(errors))
