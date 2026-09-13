import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const run = path.resolve(process.argv[2]);
const root = path.resolve(run, "../..");
const payload = JSON.parse(await fs.readFile(path.join(run, "workbook_data.json"), "utf8"));
const templatePath = path.join(root, payload.template);
const outputPath = path.join(root, payload.output);
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(templatePath));
const sheetNames = Array.from(workbook.worksheets).map(sheet => sheet.name);
const expectedNames = ["计划购电量", "调整购电量", "充放电量", "紧急购电量"];
if (JSON.stringify(sheetNames) !== JSON.stringify(expectedNames)) throw new Error(`Unexpected sheets: ${sheetNames}`);

const headerSnapshots = {};
for (const name of expectedNames) {
  const sheet = workbook.worksheets.getItem(name);
  const cols = name === "计划购电量" || name === "调整购电量" ? 147 : name === "充放电量" ? 6 : 3;
  headerSnapshots[name] = sheet.getRangeByIndexes(0, 0, 1, cols).values[0];
}

for (const name of ["计划购电量", "调整购电量"]) {
  const sheet = workbook.worksheets.getItem(name);
  const rows = payload.sheets[name];
  if (rows.length !== 334 || rows.some(row => row.length !== 147)) throw new Error(`${name} shape mismatch`);
  const existingDates = sheet.getRangeByIndexes(1, 0, 334, 1).values.map(row => row[0]);
  const dateText = value => {
    if (value instanceof Date) return value.toISOString().slice(0, 10);
    if (typeof value === "number") return new Date(Date.UTC(1899, 11, 30 + value)).toISOString().slice(0, 10);
    return String(value);
  };
  for (let i = 0; i < rows.length; i++) {
    const serialDate = existingDates[i];
    if (dateText(serialDate) !== rows[i][0]) {
      throw new Error(`${name} official date mismatch at row ${i + 2}: ${dateText(serialDate)} vs ${rows[i][0]}`);
    }
  }
  sheet.getRangeByIndexes(1, 1, 334, 146).values = rows.map(row => row.slice(1));
  sheet.getRangeByIndexes(1, 1, 334, 146).format.numberFormat = "0.000000";
}

for (const name of ["充放电量", "紧急购电量"]) {
  const sheet = workbook.worksheets.getItem(name);
  const rows = payload.sheets[name].map(row => row.map((value, col) =>
    col === 0 && value !== null ? new Date(`${value}T00:00:00Z`) : value));
  const cols = name === "充放电量" ? 6 : 3;
  sheet.getRangeByIndexes(1, 0, rows.length, cols).values = rows;
  sheet.getRangeByIndexes(1, 0, rows.length, 1).format.numberFormat = "m/d/yy";
  sheet.getRangeByIndexes(1, 0, rows.length, cols).format.font = {name: "宋体", size: 11, bold: false, color: "#000000"};
  sheet.getRangeByIndexes(1, 0, rows.length, cols).format.horizontalAlignment = "center";
  sheet.getRangeByIndexes(1, 0, rows.length, cols).format.verticalAlignment = "center";
  sheet.getRangeByIndexes(1, 0, rows.length, cols).format.rowHeight = 20;
  if (name === "充放电量") {
    sheet.getRangeByIndexes(0, 0, rows.length + 1, 1).format.columnWidth = 15;
    sheet.getRangeByIndexes(0, 1, rows.length + 1, 1).format.columnWidth = 18;
    sheet.getRangeByIndexes(0, 2, rows.length + 1, 4).format.columnWidth = 16;
    sheet.getRangeByIndexes(1, 2, rows.length, 2).format.numberFormat = "0.000000";
    sheet.getRangeByIndexes(1, 5, rows.length, 1).format.numberFormat = "0.000000";
  } else {
    sheet.getRangeByIndexes(0, 0, rows.length + 1, 1).format.columnWidth = 15;
    sheet.getRangeByIndexes(0, 1, rows.length + 1, 1).format.columnWidth = 22;
    sheet.getRangeByIndexes(0, 2, rows.length + 1, 1).format.columnWidth = 16;
    sheet.getRangeByIndexes(1, 2, rows.length, 1).format.numberFormat = "0.000000";
  }
}

for (const name of expectedNames) {
  const sheet = workbook.worksheets.getItem(name);
  const current = sheet.getRangeByIndexes(0, 0, 1, headerSnapshots[name].length).values[0];
  if (JSON.stringify(current) !== JSON.stringify(headerSnapshots[name])) throw new Error(`Header changed: ${name}`);
}

const inspections = [];
for (const [name, range] of [["计划购电量", "A1:H6"], ["调整购电量", "A1:H6"],
  ["充放电量", "A1:F14"], ["紧急购电量", "A1:C12"]]) {
  inspections.push((await workbook.inspect({kind: "table", range: `'${name}'!${range}`,
    include: "values,formulas", maxChars: 2500, tableMaxRows: 12, tableMaxCols: 8})).ndjson);
  const image = await workbook.render({sheetName: name, range, scale: 1.5, format: "png"});
  await fs.writeFile(path.join(run, `preview_${name}.png`), new Uint8Array(await image.arrayBuffer()));
}
inspections.push((await workbook.inspect({kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: {useRegex: true, maxResults: 300}, summary: "final formula error scan", maxChars: 4000})).ndjson);
await fs.writeFile(path.join(run, "workbook_inspection.txt"), inspections.join("\n"), "utf8");
await fs.mkdir(path.dirname(outputPath), {recursive: true});
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
await fs.writeFile(path.join(run, "workbook_export.json"), JSON.stringify({
  template: payload.template, output: payload.output, headers_unchanged: true,
  sheets: expectedNames, counts: payload.counts
}, null, 2) + "\n", "utf8");
console.log(JSON.stringify({output: outputPath, headers_unchanged: true, counts: payload.counts}));
