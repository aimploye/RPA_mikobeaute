const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

class FakeRange {
  constructor(sheet, row, column, numRows = 1, numColumns = 1, a1Notation = null) {
    this.sheet = sheet;
    this.row = row;
    this.column = column;
    this.numRows = numRows;
    this.numColumns = numColumns;
    this.a1Notation = a1Notation;
  }

  getSheet() { return this.sheet; }

  getA1Notation() {
    if (this.a1Notation) return this.a1Notation;
    return `${columnName_(this.column)}${this.row}`;
  }

  getValues() {
    return Array.from({ length: this.numRows }, (_, rowOffset) =>
      Array.from({ length: this.numColumns }, (_, columnOffset) =>
        this.sheet.getCell_(this.row + rowOffset, this.column + columnOffset))
    );
  }

  getValue() { return this.getValues()[0][0]; }

  setValue(value) {
    this.sheet.setCell_(this.row, this.column, value);
    this.sheet.writeLog.push({ type: 'setValue', row: this.row, column: this.column });
    return this;
  }

  setValues(values) {
    assert.equal(values.length, this.numRows);
    for (let rowOffset = 0; rowOffset < this.numRows; rowOffset++) {
      assert.equal(values[rowOffset].length, this.numColumns);
      for (let columnOffset = 0; columnOffset < this.numColumns; columnOffset++) {
        this.sheet.setCell_(
          this.row + rowOffset,
          this.column + columnOffset,
          values[rowOffset][columnOffset]
        );
      }
    }
    this.sheet.writeLog.push({
      type: 'setValues',
      row: this.row,
      column: this.column,
      numRows: this.numRows,
      numColumns: this.numColumns,
    });
    return this;
  }

  setNote(note) {
    this.sheet.notes[this.getA1Notation()] = note;
    return this;
  }

  setDataValidation() { return this; }
  setNumberFormat() { return this; }
  clearContent() {
    for (let rowOffset = 0; rowOffset < this.numRows; rowOffset++) {
      for (let columnOffset = 0; columnOffset < this.numColumns; columnOffset++) {
        this.sheet.setCell_(this.row + rowOffset, this.column + columnOffset, '');
      }
    }
    return this;
  }

  copyTo(destination) {
    // Formatting is intentionally not modelled; data writes are what these
    // regression tests protect.
    assert.ok(destination instanceof FakeRange);
    return this;
  }
}

class FakeSheet {
  constructor(name, rows, { formattedLastRow = null, maxColumns = null } = {}) {
    this.name = name;
    this.rows = rows.map((row) => row.slice());
    this.formattedLastRow = formattedLastRow || this.rows.length;
    this.maxColumns = maxColumns || Math.max(1, ...this.rows.map((row) => row.length));
    this.notes = {};
    this.writeLog = [];
  }

  getName() { return this.name; }

  getDataRange() {
    return new FakeRange(this, 1, 1, Math.max(this.formattedLastRow, this.rows.length), this.getLastColumn());
  }

  getLastRow() { return Math.max(this.formattedLastRow, this.rows.length); }
  getLastColumn() { return Math.max(this.maxColumns, ...this.rows.map((row) => row.length)); }

  getRange(rowOrA1, column, numRows = 1, numColumns = 1) {
    if (typeof rowOrA1 === 'string') {
      const match = rowOrA1.match(/^([A-Z]+)(\d+)$/);
      assert.ok(match, `Unsupported A1 notation: ${rowOrA1}`);
      return new FakeRange(this, Number(match[2]), columnNumber_(match[1]), 1, 1, rowOrA1);
    }
    return new FakeRange(this, rowOrA1, column, numRows, numColumns);
  }

  insertRowAfter(row) { this.insertRowsAfter(row, 1); }

  insertRowsAfter(row, howMany) {
    while (this.rows.length < row) this.rows.push([]);
    this.rows.splice(row, 0, ...Array.from({ length: howMany }, () => []));
    this.formattedLastRow += howMany;
  }

  insertColumnAfter(column) {
    for (const row of this.rows) row.splice(column, 0, '');
    this.maxColumns += 1;
  }

  getCell_(row, column) {
    const targetRow = this.rows[row - 1] || [];
    return targetRow[column - 1] === undefined ? '' : targetRow[column - 1];
  }

  setCell_(row, column, value) {
    while (this.rows.length < row) this.rows.push([]);
    while (this.rows[row - 1].length < column) this.rows[row - 1].push('');
    this.rows[row - 1][column - 1] = value;
    this.maxColumns = Math.max(this.maxColumns, column);
  }
}

class FakeSpreadsheet {
  constructor(sheets) {
    this.sheets = sheets;
    this.toasts = [];
  }

  getSheetByName(name) { return this.sheets.find((sheet) => sheet.getName() === name) || null; }
  getSheets() { return this.sheets; }
  toast(message) { this.toasts.push(message); }
}

function columnName_(column) {
  let name = '';
  while (column > 0) {
    const remainder = (column - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    column = Math.floor((column - 1) / 26);
  }
  return name;
}

function columnNumber_(name) {
  return name.split('').reduce((value, character) => value * 26 + character.charCodeAt(0) - 64, 0);
}

function makeSummary(items) {
  const rows = [Array(12).fill(''), Array(12).fill(''), Array(12).fill('')];
  rows[1][6] = new Date(2026, 6, 9);
  rows[2][11] = '忠孝預防\n醫學3樓';
  for (const item of items) {
    const row = Array(12).fill('');
    row.splice(0, 5, item.itemNo, item.itemCode, item.itemName, item.unit, item.category);
    row[11] = item.stock;
    rows.push(row);
  }
  return new FakeSheet('Summary', rows, { maxColumns: 13 });
}

function makeBranch(rows, options = {}) {
  const name = options.name || '忠孝預防醫學3樓';
  return new FakeSheet(
    name,
    [
      ['忠孝預防醫學3樓'],
      ['料件編號 新', '凱惠料號', '品名', '庫存', '單位', new Date(2026, 6, 9)],
      ...rows,
    ],
    { formattedLastRow: options.formattedLastRow || 3, maxColumns: 23 }
  );
}

function loadScript({ summary, branch }) {
  const spreadsheet = new FakeSpreadsheet([summary, branch]);
  const lock = { tryLock: () => true, releaseLock: () => {} };
  const triggerState = { nextId: 1, triggers: [] };
  const makeTrigger = (handlerFunction) => ({
    handlerFunction,
    id: String(triggerState.nextId++),
    getHandlerFunction() { return this.handlerFunction; },
    getUniqueId() { return this.id; },
  });
  const scriptApp = {
    AuthMode: { FULL: 'FULL', LIMITED: 'LIMITED' },
    getUserTriggers: () => triggerState.triggers.slice(),
    deleteTrigger: (trigger) => {
      triggerState.triggers = triggerState.triggers.filter((candidate) => candidate !== trigger);
    },
    newTrigger: (handlerFunction) => ({
      forSpreadsheet: () => ({
        onEdit: () => ({
          create: () => {
            const trigger = makeTrigger(handlerFunction);
            triggerState.triggers.push(trigger);
            return trigger;
          },
        }),
      }),
    }),
  };
  const context = {
    console,
    Date,
    String,
    Number,
    Math,
    SpreadsheetApp: {
      getActiveSpreadsheet: () => spreadsheet,
      flush: () => {},
      newDataValidation: () => ({
        requireValueInList: () => ({
          setAllowInvalid: () => ({ build: () => ({}) }),
        }),
      }),
    },
    LockService: { getDocumentLock: () => lock },
    ScriptApp: scriptApp,
    PropertiesService: { getDocumentProperties: () => ({ setProperty() {}, getProperty() {} }) },
  };
  vm.createContext(context);
  const scriptPath = path.join(__dirname, '../../docs/inventory_sheet_sync_apps_script.gs');
  vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), context, { filename: scriptPath });
  return { context, spreadsheet, summary, branch, triggerState };
}

function runSync(fixture) {
  fixture.context.syncInventoryHistory_();
}

test('appends missing items after the real item boundary, not after formatted blank rows', () => {
  const summary = makeSummary([
    { itemNo: 'N006', itemCode: '6200099', itemName: '新增品項', unit: '盒', category: 'X', stock: 15 },
  ]);
  const branch = makeBranch([
    ['MB3MC070001A', '6200022', '綠寶石點數', 654, 'PTS'],
  ], { formattedLastRow: 749 });
  const fixture = loadScript({ summary, branch });

  runSync(fixture);

  assert.equal(branch.rows[3][1], '6200099');
  assert.equal(branch.rows[3][5], 15);
  assert.equal(branch.rows[749], undefined, 'must not write the new item after the formatted tail');
});

test('matches reordered Summary items by key rather than by row position', () => {
  const summary = makeSummary([
    { itemNo: 'N002', itemCode: '6200002', itemName: 'B品項', unit: '盒', category: 'X', stock: 20 },
    { itemNo: 'N001', itemCode: '6200001', itemName: 'A品項', unit: '盒', category: 'X', stock: 10 },
  ]);
  const branch = makeBranch([
    ['N001', '6200001', 'A品項', 0, '盒'],
    ['N002', '6200002', 'B品項', 0, '盒'],
  ]);
  const fixture = loadScript({ summary, branch });

  runSync(fixture);

  assert.equal(branch.rows[2][5], 10);
  assert.equal(branch.rows[3][5], 20);
});

test('fails before writing when a non-zero item has an ambiguous branch key', () => {
  const summary = makeSummary([
    { itemNo: 'N001', itemCode: '6200001', itemName: 'A品項', unit: '盒', category: 'X', stock: 10 },
  ]);
  const branch = makeBranch([
    ['N001', '6200001', 'A品項', 3, '盒', 0],
    ['N001', '6200001', 'A品項', 4, '盒', 0],
  ]);
  const fixture = loadScript({ summary, branch });

  assert.throws(() => runSync(fixture), /不唯一|ambiguous|歧義/);
  assert.equal(branch.rows[2][5], 0, 'preflight failure must not partially update history');
  assert.equal(branch.rows[3][5], 0, 'preflight failure must not partially update history');
});

test('clears every duplicate row for an ambiguous zero-stock item without guessing a target', () => {
  const summary = makeSummary([
    { itemNo: 'N001', itemCode: '6200001', itemName: 'A品項', unit: '盒', category: 'X', stock: 0 },
  ]);
  const branch = makeBranch([
    ['N001', '6200001', 'A品項', 3, '盒', 3],
    ['N001', '6200001', 'A品項', 4, '盒', 4],
  ]);
  const fixture = loadScript({ summary, branch });

  runSync(fixture);

  assert.equal(branch.rows[2][5], 0);
  assert.equal(branch.rows[3][5], 0);
});

test('simple onEdit does not run the long synchronization job', () => {
  const summary = makeSummary([
    { itemNo: 'N001', itemCode: '6200001', itemName: 'A品項', unit: '盒', category: 'X', stock: 10 },
  ]);
  const branch = makeBranch([]);
  const fixture = loadScript({ summary, branch });
  const range = summary.getRange('M1');
  range.setValue('完成');
  const before = branch.rows.length;

  fixture.context.onEdit({ authMode: 'LIMITED', range });
  fixture.context.onEdit({ authMode: 'FULL', range });

  assert.equal(branch.rows.length, before);
  assert.equal(range.getValue(), '完成');
});

test('setup creates at most one current-user installable sync trigger', () => {
  const summary = makeSummary([]);
  const branch = makeBranch([]);
  const fixture = loadScript({ summary, branch });

  fixture.context.setupInventorySyncTrigger();
  fixture.context.setupInventorySyncTrigger();

  assert.equal(fixture.triggerState.triggers.length, 1);
  assert.equal(fixture.triggerState.triggers[0].getHandlerFunction(), 'inventorySyncOnEdit_');
});

test('installable handler reports success only after the batch completes', () => {
  const summary = makeSummary([
    { itemNo: 'N001', itemCode: '6200001', itemName: 'A品項', unit: '盒', category: 'X', stock: 10 },
  ]);
  const branch = makeBranch([]);
  const fixture = loadScript({ summary, branch });
  const range = summary.getRange('M1');
  range.setValue('完成');

  fixture.context.inventorySyncOnEdit_({ range });

  assert.equal(range.getValue(), '未完成');
  assert.equal(branch.rows[2][1], '6200001');
  assert.equal(branch.rows[2][5], 10);
  assert.match(range.sheet.notes.M1, /同步完成/);
});

test('installable handler marks invalid stock as failed instead of claiming success', () => {
  const summary = makeSummary([
    { itemNo: 'N001', itemCode: '6200001', itemName: 'A品項', unit: '盒', category: 'X', stock: '不是數字' },
  ]);
  const branch = makeBranch([]);
  const fixture = loadScript({ summary, branch });
  const range = summary.getRange('M1');
  range.setValue('完成');

  assert.throws(() => fixture.context.inventorySyncOnEdit_({ range }), /庫存值不是.*數字/);
  assert.equal(range.getValue(), '更新失敗');
  assert.match(range.sheet.notes.M1, /同步失敗/);
  assert.equal(branch.rows.length, 2, 'failure must not append a branch item');
});

test('rejects non-finite stock values before any branch write', () => {
  const summary = makeSummary([
    { itemNo: 'N001', itemCode: '6200001', itemName: 'A品項', unit: '盒', category: 'X', stock: Infinity },
  ]);
  const branch = makeBranch([]);
  const fixture = loadScript({ summary, branch });

  assert.throws(() => runSync(fixture), /有限數字/);
  assert.equal(branch.rows.length, 2);
});

test('rejects canonical and alias branch tabs that resolve to the same branch', () => {
  const summary = makeSummary([]);
  const branch = makeBranch([]);
  const aliasBranch = makeBranch([], { name: '忠孝預防醫學3F' });
  const fixture = loadScript({ summary, branch });
  const spreadsheetWithCollision = new FakeSpreadsheet([summary, branch, aliasBranch]);

  assert.throws(
    () => fixture.context.buildNormalizedSheetMap_(spreadsheetWithCollision),
    /分館頁籤別名重複/
  );
});
