import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const run=path.resolve(process.argv[2]);
const root=path.resolve(run,'../..');
const out=path.join(root,'outputs',`T-006-${path.basename(run)}`);
await fs.mkdir(out,{recursive:true});
const data=JSON.parse(await fs.readFile(path.join(run,'workbook_data.json'),'utf8'));
const wb=Workbook.create();
const col=n=>{let s='';for(n++;n>0;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
for(const [name,table] of Object.entries(data)){
  const s=wb.worksheets.add(name);s.showGridLines=false;
  const rows=table.rows.map(r=>r.map((v,i)=>i===0?new Date(`${v}T00:00:00Z`):v));
  const nr=rows.length+1,nc=table.headers.length;
  s.getRangeByIndexes(0,0,1,nc).values=[table.headers];
  s.getRangeByIndexes(1,0,rows.length,nc).values=rows;
  const used=s.getRangeByIndexes(0,0,nr,nc);
  used.format.font={name:'Microsoft YaHei',size:10,color:'#263746'};
  used.format.rowHeight=22;used.format.columnWidth=19;
  used.format.verticalAlignment='center';
  s.getRangeByIndexes(0,0,1,nc).format={fill:'#304E6D',font:{name:'Microsoft YaHei',size:10,color:'#FFFFFF',bold:true},wrapText:true,rowHeight:42};
  s.getRangeByIndexes(1,1,rows.length,nc-1).setNumberFormat('#,##0.00');
  s.getRangeByIndexes(1,0,rows.length,1).setNumberFormat('yyyy-mm-dd');
  s.getRangeByIndexes(0,0,nr,1).format.columnWidth=16;
  s.getRangeByIndexes(0,0,nr,1).format.horizontalAlignment='center';
  s.getRangeByIndexes(0,0,1,nc).format.horizontalAlignment='center';
  if(name.includes('充放电')||name==='指定日表2'){
    s.getRangeByIndexes(1,1,rows.length,1).format.horizontalAlignment='center';
    s.getRangeByIndexes(1,4,rows.length,1).format.horizontalAlignment='center';
  }
  s.freezePanes.freezeRows(1);
  if(name==='计划购电量'){
    s.getRangeByIndexes(1,145,rows.length,1).formulas=rows.map((_,i)=>[`=SUM(B${i+2}:${col(144)}${i+2})`]);
  }
  if(name==='费用汇总'){
    s.getRangeByIndexes(1,3,rows.length,1).formulas=rows.map((_,i)=>[`=B${i+2}+C${i+2}`]);
    s.getRangeByIndexes(1,9,rows.length,1).setNumberFormat('0.00');
    const last=nr+1;s.getRange(`A${last}`).values=[['全年合计']];
    for(let c=1;c<=6;c++)s.getRange(`${col(c)}${last}`).formulas=[[`=SUM(${col(c)}2:${col(c)}${nr})`]];
    s.getRange(`H${last}`).formulas=[['=H2']];s.getRange(`I${last}`).formulas=[[`=I${nr}`]];
    s.getRange(`A${last}:J${last}`).format={fill:'#E7EDF4',font:{bold:true},numberFormat:'#,##0.00'};
  }
}
wb.recalculate();
const inspections=[];
for(const name of Object.keys(data)){
  const range=`A1:${col(Math.min(7,data[name].headers.length-1))}${Math.min(8,data[name].rows.length+1)}`;
  inspections.push((await wb.inspect({kind:'table',range:`'${name}'!${range}`,include:'values,formulas',maxChars:1200,tableMaxRows:4,tableMaxCols:5})).ndjson);
  const im=await wb.render({sheetName:name,range,scale:1.5,format:'png'});
  await fs.writeFile(path.join(run,`preview_${name}.png`),new Uint8Array(await im.arrayBuffer()));
}
inspections.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',options:{useRegex:true,maxResults:20},maxChars:1500})).ndjson);
await fs.writeFile(path.join(run,'workbook_inspection.txt'),inspections.join('\n'));
const exported=await SpreadsheetFile.exportXlsx(wb);const target=path.join(out,'result2.xlsx');await exported.save(target);
await fs.writeFile(path.join(run,'workbook_export.json'),JSON.stringify({path:target,repository_relative_path:path.relative(root,target).replaceAll('\\','/'),sheets:Object.keys(data)},null,2));
console.log('Excel exported:',target);
