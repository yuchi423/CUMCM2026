"""Extract the selected Q3 route from audited results without rerunning optimization."""
from pathlib import Path
import csv
import gzip
import hashlib
import json
import math
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = 'rolling_margin'


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    assert rows, path
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(run_arg, out_arg):
    run, out = ROOT / run_arg, ROOT / out_arg
    audit = json.loads((run / 'audit_summary.json').read_text(encoding='utf8'))
    assert audit['pass_']
    config = json.loads((ROOT / 'configs/q3_models.json').read_text(encoding='utf8'))
    sources = [run / 'audit_summary.json', run / 'run.json', ROOT / 'configs/q3_models.json']
    tables = {}
    for name in ['summary_tables', 'daily_summary', 'periods', 'paper_dates_summary', 'issue_summary']:
        path = run / 'results' / (name + '.csv')
        tables[name] = [r for r in read_csv(path) if r['strategy'] == STRATEGY]
        sources.append(path)
    summary = tables['summary_tables'][0]
    daily = tables['daily_summary']
    assert len(daily) == int(summary['days']) == 334
    errors = {}
    for key in ['ordinary_cost', 'adjustment_cost', 'emergency_cost', 'total_cost', 'ordinary_kwh', 'emergency_kwh']:
        errors[key] = abs(math.fsum(float(r[key]) for r in daily) - float(summary[key]))
        assert errors[key] < 1e-6, (key, errors[key])
    assert abs(sum(float(summary[k]) for k in ['ordinary_cost', 'adjustment_cost', 'emergency_cost']) - float(summary['total_cost'])) < 1e-6
    dates = set(config['paper_dates'])
    dispatch_path = run / 'details' / (STRATEGY + '_dispatch.csv.gz')
    versions_path = run / 'details' / (STRATEGY + '_plan_versions.jsonl.gz')
    sources.extend([dispatch_path, versions_path])
    with gzip.open(dispatch_path, 'rt', encoding='utf8', newline='') as f:
        dispatch = [r for r in csv.DictReader(f) if r['date'] in dates]
    assert len(dispatch) == len(dates) * 144
    plans = []
    versions = {}
    with gzip.open(versions_path, 'rt', encoding='utf8') as f:
        for line in f:
            v = json.loads(line)
            if v['date'] not in dates:
                continue
            versions[(v['date'], v['issue_hour'])] = v
            recomputed = 0.0
            for i, new in enumerate(v['new']):
                slot = v['start_slot'] + i
                old = None if v['old'] is None else v['old'][i]
                fee = 0.0 if old is None else 0.5 * v['price'][i] * abs(new - old)
                recomputed += fee
                end_minutes = (slot + 1) * 10
                plans.append(dict(date=v['date'], issue_hour=v['issue_hour'], slot=slot,
                                  interval_end=f'{end_minutes // 60:02d}:{end_minutes % 60:02d}',
                                  price_yuan_per_kwh=v['price'][i], old_kwh='' if old is None else old,
                                  new_kwh=new, adjustment_cost_yuan=fee))
            assert abs(recomputed - v['fee']) < 1e-6
    assert len(versions) == len(dates) * 4
    for d in dates:
        previous = None
        for hour in [0, 6, 12, 18]:
            v = versions[(d, hour)]
            if previous is not None:
                assert max(abs(a - b) for a, b in zip(v['old'], previous['new'][36:])) < 1e-6
            previous = v
        rows = [r for r in dispatch if r['date'] == d]
        for row in rows:
            v = versions[(d, int(row['issue_hour']))]
            assert abs(float(row['ordinary_kwh']) - v['new'][int(row['slot']) - v['start_slot']]) < 1e-6
        expected = next(r for r in tables['paper_dates_summary'] if r['date'] == d)
        for field in ['ordinary_cost', 'emergency_cost']:
            assert abs(math.fsum(float(r[field]) for r in rows) - float(expected[field])) < 1e-6
        assert abs(sum(versions[(d, h)]['fee'] for h in [0, 6, 12, 18]) - float(expected['adjustment_cost'])) < 1e-6
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        write_csv(out / (name + '.csv'), rows)
    write_csv(out / 'paper_dates_dispatch.csv', dispatch)
    write_csv(out / 'paper_dates_plan_versions.csv', plans)
    manifest = dict(strategy=STRATEGY, source_run=run.relative_to(ROOT).as_posix(),
                    horizon='issue time through current-day 24:00',
                    parameters=config, validation=dict(pass_=True, daily_rows=len(daily),
                    paper_dispatch_rows=len(dispatch), paper_plan_versions=len(versions), sum_errors=errors),
                    source_sha256={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps(manifest['validation'], ensure_ascii=False))


if __name__ == '__main__':
    main(*sys.argv[1:])
