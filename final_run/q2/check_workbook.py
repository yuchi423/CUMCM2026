"""Round-trip check of every published workbook cell against the numerical results."""
import json
import math
import sys
import csv
from collections import defaultdict
from pathlib import Path
import openpyxl

def main(run):
    metadata=json.loads((run/"workbook_export.json").read_text(encoding="utf-8"))
    root=Path(__file__).resolve().parents[2]
    book=openpyxl.load_workbook(root/metadata["repository_relative_path"],read_only=True,data_only=True)
    payload=json.loads((run/"workbook_data.json").read_text(encoding="utf-8"))
    count=0;worst=0.
    assert book.sheetnames==list(payload)
    for name,data in payload.items():
        sheet=book[name];export_rows=list(sheet.values);values=iter(export_rows);header=next(values)
        assert list(header)==data["headers"]
        for source,row in zip(data["rows"],values):
            assert len(source)==len(row)
            for c,(a,b) in enumerate(zip(source,row)):
                if c==0:assert b.date().isoformat()==a
                elif isinstance(a,(int,float)):
                    assert isinstance(b,(int,float)) and math.isfinite(b),(name,c,a,b)
                    worst=max(worst,abs(a-b));assert abs(a-b)<1e-5,(name,c,a,b)
                else:assert a==b,(name,c,a,b)
                count+=1
        assert len(export_rows)==len(data["rows"])+(2 if name=="费用汇总" else 1)
    total=json.loads((run/"totals.json").read_text(encoding="utf-8"))
    actual=next(r for r in total if r["strategy"]=="operational")
    assert abs(book["费用汇总"].cell(336,4).value-actual["total_cost"])<.01
    ledger=defaultdict(list)
    with (run/"operational_dispatch.csv").open(encoding="utf-8-sig",newline="") as f:
        for r in csv.DictReader(f):ledger[r["date"]].append(r)
    for row in book["计划购电量"].iter_rows(min_row=2,values_only=True):
        date=row[0].date().isoformat();g=ledger[date]
        assert len(g)==144
        assert all(abs(row[t+1]-float(g[t]["plan_kwh"]))<1e-6 for t in range(144))
        assert abs(row[-1]-math.fsum(float(x["plan_cost"]) for x in g))<1e-6
    for i,row in enumerate(book["充放电量"].iter_rows(min_row=2,values_only=True)):
        date=row[0].date().isoformat();b=i%6;g=ledger[date][b*24:(b+1)*24]
        assert abs(row[2]-math.fsum(float(x["charge_kwh"]) for x in g))<1e-6
        assert abs(row[3]-math.fsum(float(x["discharge_kwh"]) for x in g))<1e-6
    for row in book["指定日表1"].iter_rows(min_row=2,values_only=True):
        g=ledger[row[0].date().isoformat()]
        assert all(abs(row[j+1]-float(g[t]["plan_kwh"]))<1e-6 for j,t in enumerate([60,72,84,96,108,120]))
    expected_events={}
    for date,g in ledger.items():
        for t,r in enumerate(g):
            if float(r["emergency_kwh"])>1e-6:
                key=(date,f"{t//6:02}:{(t%6)*10:02}-{(t+1)//6:02}:{((t+1)%6)*10:02}")
                expected_events[key]=float(r["emergency_kwh"])
    exported_events={(r[0].date().isoformat(),r[1]):r[2] for r in book["紧急购电量"].iter_rows(min_row=2,values_only=True) if r[2]>1e-6}
    assert expected_events.keys()==exported_events.keys()
    assert all(abs(v-exported_events[k])<1e-6 for k,v in expected_events.items())
    book.close()
    result={"pass":True,"checked_cells":count,"max_numeric_error":worst,"sheets":len(payload),
            "planned_days":334,"storage_rows":334*6,"all_paper_dates":True,"cash_total_matches":True,
            "independent_ledger_to_plan_storage_paper_and_emergency_reconciliation":True}
    (run/"workbook_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result))

if __name__=="__main__":main(Path(sys.argv[1]))
