import fs from 'node:fs/promises';
import path from 'node:path';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const directory = path.resolve(process.argv[2]);
const root = path.resolve(directory, '../..');
const verifyFinal = process.argv.includes('--verify-final');
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(verifyFinal ? path.join(directory,'result4-2.xlsx') : path.join(root, 'data/templates/result4-2.xlsx')));
const names = ['计划购电量', '充放电量', '紧急购电量'];
const ranges = ['A1:G5', 'A1:F8', 'A1:C8'];
const readOnly = process.argv.includes('--inspect-only') || verifyFinal;
if (!readOnly) {
  const data = JSON.parse(await fs.readFile(path.join(directory, 'workbook_data.json'), 'utf8'));
  const plan = workbook.worksheets.getItem(names[0]);
  const storage = workbook.worksheets.getItem(names[1]);
  const emergency = workbook.worksheets.getItem(names[2]);
  const before = names.map((name, i) => workbook.worksheets.getItem(name).getRange(['A1:EQ1','A1:F1','A1:C1'][i]).values);
  plan.getRange('B2:EQ335').values = data.sheets[names[0]].rows.map(row => row.slice(1));
  const pattern = storage.getRange('A2:F7').values;
  const sr = data.sheets[names[1]].rows.map((row, i) => [i%6 === 0 ? new Date(`${row[0]}T00:00:00Z`) : null, row[1], row[2], row[3], pattern[i%6][4], row[5]]);
  storage.getRange(`A2:F${sr.length+1}`).values = sr;
  let previous = null;
  const er = data.sheets[names[2]].rows.map(row => {
    const value = [row[0] === previous ? null : new Date(`${row[0]}T00:00:00Z`), row[1], row[2]];
    previous = row[0]; return value;
  });
  emergency.getRange(`A2:C${er.length+1}`).values = er;
  workbook.recalculate();
  const after = names.map((name, i) => workbook.worksheets.getItem(name).getRange(['A1:EQ1','A1:F1','A1:C1'][i]).values);
  if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error('Template header changed');
  const scan = await workbook.inspect({kind:'match', searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!', options:{useRegex:true,maxResults:20}});
  await fs.writeFile(path.join(directory,'formula_scan.ndjson'), scan.ndjson);
}
for (let i=0; i<names.length; i++) {
  const image = await workbook.render({sheetName:names[i],range:ranges[i],scale:1.2,format:'png'});
  await fs.writeFile(path.join(directory,`${verifyFinal?'final':readOnly?'template':'filled'}-${i+1}.png`),new Uint8Array(await image.arrayBuffer()));
}
if (!readOnly) {
  await (await SpreadsheetFile.exportXlsx(workbook)).save(path.join(directory,'result4-2.xlsx'));
  console.log('Filled original three-sheet template');
}
