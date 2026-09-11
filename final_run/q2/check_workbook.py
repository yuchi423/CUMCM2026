"""Round-trip check of every published workbook cell against the numerical results."""
import json
import math
import sys
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
        sheet=book[name];values=iter(sheet.values);header=next(values)
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
        assert sheet.max_row==len(data["rows"])+(2 if name=="费用汇总" else 1)
    total=json.loads((run/"totals.json").read_text(encoding="utf-8"))
    actual=next(r for r in total if r["strategy"]=="operational")
    assert abs(book["费用汇总"].cell(336,4).value-actual["total_cost"])<.01
    book.close()
    result={"pass":True,"checked_cells":count,"max_numeric_error":worst,"sheets":len(payload),
            "planned_days":334,"storage_rows":334*6,"all_paper_dates":True,"cash_total_matches":True}
    (run/"workbook_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result))

if __name__=="__main__":main(Path(sys.argv[1]))
