import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const root=path.resolve(process.argv[2]);
const temp=path.join(root,'tmp','q3-workbook');
const target=path.join(root,'output','result3.xlsx');
const data=JSON.parse(await fs.readFile(path.join(temp,'workbook_data.json'),'utf8'));
const wb=Workbook.create();
const col=n=>{let s='';for(n++;n>0;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};

for(const [name,table] of Object.entries(data)){
  const sheet=wb.worksheets.add(name);
  sheet.showGridLines=false;
  const rows=table.rows.map(r=>r.map((v,i)=>i===0&&typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)?new Date(`${v}T00:00:00Z`):v));
  const nr=rows.length+1,nc=table.headers.length;
  sheet.getRangeByIndexes(0,0,1,nc).values=[table.headers];
  sheet.getRangeByIndexes(1,0,rows.length,nc).values=rows;
  const used=sheet.getRangeByIndexes(0,0,nr,nc);
  used.format.font={name:'Microsoft YaHei',size:10,color:'#263746'};
  used.format.rowHeight=22;
  used.format.verticalAlignment='center';
  used.format.columnWidth=name==='计划购电量'||name==='调整购电量'?15:19;
  sheet.getRangeByIndexes(0,0,1,nc).format={fill:'#304E6D',font:{name:'Microsoft YaHei',size:10,color:'#FFFFFF',bold:true},wrapText:true,rowHeight:42,horizontalAlignment:'center'};
  sheet.getRangeByIndexes(1,0,rows.length,1).setNumberFormat('yyyy-mm-dd');
  sheet.getRangeByIndexes(0,0,nr,1).format.columnWidth=16;
  sheet.getRangeByIndexes(0,0,nr,1).format.horizontalAlignment='center';
  if(nc>1) sheet.getRangeByIndexes(1,1,rows.length,nc-1).setNumberFormat('#,##0.00');
  sheet.freezePanes.freezeRows(1);
  if(name==='计划购电量'||name==='调整购电量'){
    sheet.freezePanes.freezeColumns(1);
    sheet.getRangeByIndexes(1,145,rows.length,1).formulas=rows.map((_,i)=>[`=SUM(B${i+2}:EO${i+2})`]);
    sheet.getRangeByIndexes(0,145,nr,2).format.fill='#E7EDF4';
    sheet.getRangeByIndexes(0,145,1,2).format={fill:'#304E6D',font:{name:'Microsoft YaHei',size:10,color:'#FFFFFF',bold:true},wrapText:true,rowHeight:42,horizontalAlignment:'center'};
  }
  if(name==='费用汇总'){
    sheet.getRangeByIndexes(1,5,rows.length,1).formulas=rows.map((_,i)=>[`=C${i+2}+D${i+2}+E${i+2}`]);
    const totalRow=nr+1;
    sheet.getRange(`A${totalRow}`).values=[['全年合计']];
    for(let c=1;c<=8;c++) sheet.getRange(`${col(c)}${totalRow}`).formulas=[[`=SUM(${col(c)}2:${col(c)}${nr})`]];
    sheet.getRange(`J${totalRow}`).formulas=[['=J2']];
    sheet.getRange(`K${totalRow}`).formulas=[[`=K${nr}`]];
    sheet.getRange(`L${totalRow}:M${totalRow}`).formulas=[[`=SUM(L2:L${nr})`,`=SUM(M2:M${nr})`]];
    sheet.getRange(`A${totalRow}:M${totalRow}`).format={fill:'#E7EDF4',font:{name:'Microsoft YaHei',size:10,color:'#263746',bold:true},numberFormat:'#,##0.00'};
    const noteRow=totalRow+1;
    sheet.getRange(`A${noteRow}`).values=[['说明']];
    sheet.getRange(`B${noteRow}`).values=[['数据来源：题目附件1电价、附件2实际供需及附件3光伏预测；第三问方向2 rolling_margin；统计期2025-02-01至2025-12-31。']];
    sheet.getRange(`A${noteRow}:M${noteRow}`).format={fill:'#F7F8FA',font:{name:'Microsoft YaHei',size:9,color:'#5B6573',italic:true},wrapText:true,rowHeight:36};
    sheet.getRange(`B${noteRow}`).format.columnWidth=42;
  }
  if(name==='充放电量'||name==='指定日表2'){
    sheet.getRangeByIndexes(1,1,rows.length,1).format.horizontalAlignment='center';
    sheet.getRangeByIndexes(1,4,rows.length,1).format.horizontalAlignment='center';
  }
}
wb.recalculate();
const inspections=[];
for(const name of Object.keys(data)){
  const table=data[name];
  const endCol=col(Math.min(9,table.headers.length-1));
  const endRow=Math.min(8,table.rows.length+1);
  inspections.push((await wb.inspect({kind:'table',range:`'${name}'!A1:${endCol}${endRow}`,include:'values,formulas',maxChars:1600,tableMaxRows:5,tableMaxCols:10})).ndjson);
  const preview=await wb.render({sheetName:name,range:`A1:${endCol}${endRow}`,scale:1.5,format:'png'});
  await fs.writeFile(path.join(temp,`preview_${name}.png`),new Uint8Array(await preview.arrayBuffer()));
}
inspections.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:100},maxChars:2500})).ndjson);
await fs.writeFile(path.join(temp,'workbook_inspection.ndjson'),inspections.join('\n'));
await fs.mkdir(path.dirname(target),{recursive:true});
const output=await SpreadsheetFile.exportXlsx(wb);
await output.save(target);
console.log('Excel exported:',target);
