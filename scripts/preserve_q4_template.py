"""Preserve native template XML/styles where artifact-tool export normalizes them.

Artifact Tool performs initial authoring/rendering. This values-only finalizer
addresses its demonstrated loss of original fonts/borders and extension formats.
openpyxl is used only for independent read-only verification.
"""
import copy
import csv
import datetime as dt
import json
import math
import sys
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
import openpyxl
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('', NS)
def tag(name): return '{'+NS+'}'+name
def serial(date): return (dt.date.fromisoformat(date)-dt.date(1899,12,30)).days
def put(cell, value):
    for child in list(cell): cell.remove(child)
    cell.attrib.pop('t', None)
    if value is None: return
    if isinstance(value,str):
        cell.set('t','inlineStr'); ET.SubElement(ET.SubElement(cell,tag('is')),tag('t')).text=value
    else: ET.SubElement(cell,tag('v')).text=repr(value)

def main(directory):
    data=json.loads((directory/'workbook_data.json').read_text(encoding='utf-8'))
    source=ROOT/'data/templates/result4-2.xlsx'; target=directory/'result4-2.xlsx'
    openpyxl_source=openpyxl.load_workbook(source)
    names=['计划购电量','充放电量','紧急购电量']
    blocks=[]
    blocks.append([row for row in data['sheets'][names[0]]['rows']])
    blocks.append([[serial(r[0]) if i%6==0 else None,r[1],r[2],r[3],0 if i%6==0 else '24:00' if i%6==1 else None,r[5]] for i,r in enumerate(data['sheets'][names[1]]['rows'])])
    previous=None; events=[]
    for row in data['sheets'][names[2]]['rows']:
        events.append([serial(row[0]) if row[0]!=previous else None,row[1],row[2]]); previous=row[0]
    blocks.append(events)
    replacements={}
    with zipfile.ZipFile(source) as original:
        for index,values in enumerate(blocks):
            filename=f'xl/worksheets/sheet{index+1}.xml'
            tree=ET.fromstring(original.read(filename)); body=tree.find(tag('sheetData'))
            existing={int(row.get('r')):row for row in body}
            patterns=[copy.deepcopy(existing[r]) for r in (range(2,8) if index==1 else [2,3] if index==2 else [2])]
            for i,record in enumerate(values):
                number=i+2
                if number not in existing:
                    row=copy.deepcopy(patterns[i%len(patterns)]); row.set('r',str(number))
                    for cell in row: cell.set('r',''.join(filter(str.isalpha,cell.get('r')))+str(number))
                    body.append(row); existing[number]=row
                row=existing[number]; cells={c.get('r'):c for c in row}
                for j,value in enumerate(record):
                    reference=f'{get_column_letter(j+1)}{number}'
                    if index==0 and j==0: continue  # retain native date labels exactly
                    if reference not in cells:
                        cell=ET.Element(tag('c'),{'r':reference}); row.append(cell)
                    else: cell=cells[reference]
                    # Newly filled date labels in formerly General-format cells
                    # use ISO identifiers so the untouched format cannot show serials.
                    if index>0 and j==0 and value is not None:
                        label=(dt.date(1899,12,30)+dt.timedelta(days=value)).isoformat()
                        template_cell=openpyxl_source.worksheets[index].cell(number,1) if number<=openpyxl_source.worksheets[index].max_row else openpyxl_source.worksheets[index].cell(2+i%len(patterns),1)
                        if template_cell.number_format=='General': value=label; record[j]=label
                    put(cell,value)
                row[:]=sorted(row,key=lambda c: openpyxl.utils.cell.column_index_from_string(''.join(filter(str.isalpha,c.get('r')))))
            tree.find(tag('dimension')).set('ref',f'A1:{get_column_letter(len(values[0]))}{len(values)+1}')
            replacements[filename]=ET.tostring(tree,encoding='utf-8',xml_declaration=True)
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as final:
            for item in original.infolist(): final.writestr(item,replacements.get(item.filename,original.read(item.filename)))
    a=openpyxl.load_workbook(source); b=openpyxl.load_workbook(target)
    assert a.sheetnames==b.sheetnames
    for s in a:
        for cell in s[1]: assert cell.value==b[s.title].cell(cell.row,cell.column).value
        for row in s:
            for cell in row:
                other=b[s.title].cell(cell.row,cell.column)
                for attribute in ['font','fill','border','alignment','protection','number_format']:
                    assert copy.copy(getattr(cell,attribute))==copy.copy(getattr(other,attribute)),(s.title,cell.coordinate,attribute)
    maximum=0.
    for i,record in enumerate(blocks[0]):
        assert b.worksheets[0].cell(i+2,1).value.date().isoformat()==record[0]
    for index,records in enumerate(blocks):
        sheet=b.worksheets[index]
        for i,record in enumerate(records):
            for j,expected in enumerate(record):
                if index==0 and j==0: continue
                actual=sheet.cell(i+2,j+1).value
                if isinstance(actual,(dt.datetime,dt.time)):
                    actual=serial(actual.date().isoformat()) if isinstance(actual,dt.datetime) else 0
                if isinstance(expected,(int,float)):
                    error=abs(actual-expected); maximum=max(maximum,error); assert error<1e-7
                else: assert actual==expected,(sheet.title,i+2,j+1,actual,expected)
    with (directory/'template_time_mapping.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['Excel列','原始表头（保留）','实际当天时段（已确认位置映射）'])
        for t in range(144): writer.writerow([get_column_letter(t+2),a.worksheets[0].cell(1,t+2).value,data['sheets'][names[0]]['headers'][t+1]])
    totals={key:math.fsum(r[col] for r in data['sheets']['费用汇总']['rows']) for key,col in [('plan_cost',1),('emergency_cost',2),('cash_cost',3)]}
    experiment=ROOT/'experiments/q4-saa-strict-full-20260913-01'
    # The finalizer is also used by new runs: resolve their source from metadata.
    source_experiment=Path(data.get('source_experiment',experiment))
    if not source_experiment.is_absolute(): source_experiment=ROOT/source_experiment
    with (source_experiment/'results/summary_tables.csv').open(encoding='utf-8-sig',newline='') as stream:
        annual=next(r for r in csv.DictReader(stream) if r['method']==data['method'])
    for key,column in [('plan_cost','plan_cost'),('emergency_cost','emergency_cost'),('cash_cost','total_cost')]:
        assert abs(totals[key]-float(annual[column]))<1e-6
    assert abs(math.fsum(r[-2] for r in blocks[0])-float(annual['plan_kwh']))<1e-6
    assert abs(math.fsum(r[2] for r in blocks[2])-float(annual['emergency_kwh']))<1e-6
    result={'pass':True,'selected_method':data['method'],'days':334,'slots':48096,'sheet_count':3,'headers_unchanged':True,'original_styles_unchanged':True,'maximum_cell_error':maximum,'plan_kwh':math.fsum(r[-2] for r in blocks[0]),**totals,'storage_rows':len(blocks[1]),'emergency_rows':len(blocks[2]),'time_mapping':'Original shifted headers preserved; confirmed positional mapping in template_time_mapping.csv.'}
    (directory/'workbook_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__': main(Path(sys.argv[1]).resolve())
