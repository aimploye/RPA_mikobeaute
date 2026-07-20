/**
 * 庫存紀錄表同步腳本。
 *
 * 使用方式：
 * 1. 在 Google Sheet 的 Apps Script 貼上本檔內容。
 * 2. Summary!M1 設為下拉選單：未完成、完成、更新失敗，預設未完成。
 * 3. 在 Apps Script 編輯器執行一次 setupInventorySyncTrigger() 並完成授權。
 * 4. 同仁更新 Summary!G2 與各分館庫存後，將 Summary!M1 改為完成。
 * 5. 安裝型編輯觸發器會同步各分館頁籤；完成後把 M1 改回未完成，失敗則改為更新失敗。
 *
 * 設計原則：
 * - 只讓安裝型觸發器執行同步；保留的 simple onEdit 只負責快速返回，避免 30 秒上限。
 * - 分館欄位以 Summary 第 3 列的可見標題尋找，不依賴固定欄位順序。
 * - 品項以凱惠料號 + 品名匹配；同 key 不唯一時才使用料件編號 新 + 凱惠料號 + 品名。
 * - 所有分館先完成預檢，再寫入；預檢有問題時不修改任何日期欄。
 * - 以 A:C 真實品項資料找資料尾端，不使用受格式化空白列污染的 getLastRow() 當新增位置。
 * - Summary 有庫存且分館頁籤沒有該品項時，一次批次新增品項列。
 * - 分館頁籤的舊品項列保留歷史資料，但本次日期欄清為 0。
 * - Summary 空白視為 0；整個分館欄位空白視為尚未營運/尚未填寫，不動作。
 */

const INVENTORY_SYNC = {
  summarySheetName: 'Summary',
  statusCellA1: 'M1',
  pendingValue: '未完成',
  doneValue: '完成',
  failedValue: '更新失敗',
  branchHeaderRow: 3,
  firstDataRow: 4,
  branchFirstDataRow: 3,
  firstBranchColumn: 7, // G；只作為掃描起點，不代表分館固定在 G:L。
  firstHistoryColumn: 6, // F；分館 A:E 是品項欄，日期欄從 F 起。
  dateCellA1: 'G2',
  branchSheetAliases: {
    '站前4樓': ['站前4樓', '站前4F', '站前四樓'],
    '站前11樓': ['站前11樓', '站前11F', '站前十一樓'],
    '忠孝7樓': ['忠孝7樓', '忠孝7F', '忠孝七樓'],
    '忠孝健康7樓': ['忠孝健康7樓', '忠孝健康7F', '忠孝健康七樓'],
    '忠孝國際醫學3樓': [
      '忠孝國際醫學3樓',
      '忠孝國際 醫學3樓',
      '忠孝國際醫學 3樓',
      '忠孝國際醫學3F',
      '忠孝國際醫學三樓',
    ],
    '忠孝預防醫學3樓': [
      '忠孝預防醫學3樓',
      '忠孝預防 醫學3樓',
      '忠孝預防醫學 3樓',
      '忠孝預防醫學3F',
      '忠孝預防醫學三樓',
    ],
  },
};

/**
 * Simple trigger 不執行長工作；setupInventorySyncTrigger() 會建立真正的安裝型觸發器。
 * 這個函式保留是為了避免同仁貼上程式後誤以為 simple trigger 會在 30 秒內完成大型同步。
 */
function onEdit(e) {
  // 故意不在 simple trigger 執行任何同步；請使用下方安裝型 handler。
  return;
}

/**
 * 只由 setupInventorySyncTrigger() 建立的安裝型編輯觸發器呼叫。
 */
function inventorySyncOnEdit_(e) {
  handleInventoryEdit_(e);
}

/**
 * 首次安裝或修復時手動執行一次。
 * 只刪除本腳本自己的 handler，不碰同一專案其他觸發器。
 */
function setupInventorySyncTrigger() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const existing = ScriptApp.getUserTriggers(ss).filter((trigger) =>
    trigger.getHandlerFunction() === 'inventorySyncOnEdit_'
  );
  if (existing.length === 1) {
    ss.toast('庫存同步安裝型觸發器已存在，未重複建立。', 'POSReportBot', 10);
    return;
  }

  // 先建立新 trigger，再刪除目前授權使用者名下的重複 trigger；建立失敗時不會先拆掉唯一舊 trigger。
  const created = ScriptApp.newTrigger('inventorySyncOnEdit_')
    .forSpreadsheet(ss)
    .onEdit()
    .create();
  const createdId = created.getUniqueId();
  ScriptApp.getUserTriggers(ss).forEach((trigger) => {
    if (trigger.getHandlerFunction() === 'inventorySyncOnEdit_' &&
        trigger.getUniqueId() !== createdId) {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  const remaining = ScriptApp.getUserTriggers(ss).filter((trigger) =>
    trigger.getHandlerFunction() === 'inventorySyncOnEdit_'
  );
  if (remaining.length !== 1 || remaining[0].getUniqueId() !== createdId) {
    throw new Error('無法確認目前授權使用者名下只有一個庫存同步安裝型觸發器。');
  }
  ss.toast('庫存同步安裝型觸發器已建立。', 'POSReportBot', 10);
}

function handleInventoryEdit_(e) {
  const range = e && e.range;
  if (!range) return;
  const sheet = range.getSheet();
  if (sheet.getName() !== INVENTORY_SYNC.summarySheetName) return;
  if (range.getA1Notation() !== INVENTORY_SYNC.statusCellA1) return;
  if (String(range.getValue()).trim() !== INVENTORY_SYNC.doneValue) return;

  const lock = LockService.getDocumentLock();
  if (!lock.tryLock(1000)) {
    markSyncFailure_(range, SpreadsheetApp.getActiveSpreadsheet(), '目前已有另一個同步程序正在執行。');
    return;
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  try {
    ensureStatusValidation_(range);
    setSyncStatus_(range, ss, '同步執行中，正在預檢各分館與品項 key，請稍候。');
    const result = syncInventoryHistory_();
    range.setValue(INVENTORY_SYNC.pendingValue);
    range.setNote(
      `同步完成：${new Date().toLocaleString('zh-TW')}；` +
      `更新 ${result.updatedBranches} 個分館，新增 ${result.appendedItems} 個品項。`
    );
    ss.toast('庫存同步完成，狀態已改回未完成。', 'POSReportBot', 10);
    SpreadsheetApp.flush();
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    markSyncFailure_(range, ss, message);
    throw error;
  } finally {
    // 確保狀態與批次寫入已送出，再釋放文件鎖，避免下一次執行讀到半套狀態。
    SpreadsheetApp.flush();
    lock.releaseLock();
  }
}

function setSyncStatus_(range, ss, message) {
  range.setNote(message);
  ss.toast(message, 'POSReportBot', 10);
  SpreadsheetApp.flush();
}

function markSyncFailure_(range, ss, message) {
  range.setValue(INVENTORY_SYNC.failedValue);
  range.setNote(`同步失敗：${message}`);
  ss.toast(`庫存同步失敗：${message}`, 'POSReportBot', 10);
  SpreadsheetApp.flush();
}

function ensureStatusValidation_(range) {
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList([
      INVENTORY_SYNC.pendingValue,
      INVENTORY_SYNC.doneValue,
      INVENTORY_SYNC.failedValue,
    ], true)
    .setAllowInvalid(false)
    .build();
  range.setDataValidation(rule);
}

/**
 * 先建立所有分館 plan，再一次套用。
 * 任何分館的資料或 key 預檢失敗，都在第一個日期欄寫入前拋錯。
 */
function syncInventoryHistory_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const summary = requireSheet_(ss, INVENTORY_SYNC.summarySheetName);
  const inventoryDate = summary.getRange(INVENTORY_SYNC.dateCellA1).getValue();
  if (!(inventoryDate instanceof Date) || Number.isNaN(inventoryDate.getTime())) {
    throw new Error(`${INVENTORY_SYNC.summarySheetName}!${INVENTORY_SYNC.dateCellA1} 不是有效日期。`);
  }

  const summaryValues = readSummaryValues_(summary);
  const summaryItems = buildSummaryItems_(summaryValues);
  const branchColumns = findBranchColumns_(summaryValues);
  const sheetByName = buildNormalizedSheetMap_(ss);
  const plans = [];
  const issues = [];

  branchColumns.forEach((branch) => {
    const branchValues = summaryItems.map((item) =>
      toNumber_(summaryValues[item.rowIndex][branch.column - 1])
    );
    const hasAnyInput = summaryItems.some((item) =>
      hasInput_(summaryValues[item.rowIndex][branch.column - 1])
    );
    const hasAnyNonZero = branchValues.some((value) => value !== 0);
    if (!hasAnyInput && !hasAnyNonZero) return;

    const branchSheet = resolveBranchSheet_(sheetByName, branch.name);
    if (!branchSheet) {
      issues.push(`找不到分館頁籤：${branch.name}。目前可見頁籤：${visibleSheetNames_(ss)}`);
      return;
    }

    try {
      plans.push(buildBranchPlan_(branchSheet, branch.name, inventoryDate, summaryItems, branchValues));
    } catch (error) {
      issues.push(error && error.message ? error.message : String(error));
    }
  });

  if (issues.length) throw new Error(issues.join('\n'));

  let appendedItems = 0;
  plans.forEach((plan) => {
    applyBranchPlan_(plan);
    appendedItems += plan.appendItems.length;
  });
  return { updatedBranches: plans.length, appendedItems };
}

function readSummaryValues_(summary) {
  const rowCount = Math.max(summary.getLastRow(), INVENTORY_SYNC.firstDataRow - 1);
  const columnCount = Math.max(summary.getLastColumn(), INVENTORY_SYNC.firstBranchColumn);
  return summary.getRange(1, 1, rowCount, columnCount).getValues();
}

function findBranchColumns_(summaryValues) {
  const header = summaryValues[INVENTORY_SYNC.branchHeaderRow - 1] || [];
  const branches = [];
  const seen = {};
  for (let column = INVENTORY_SYNC.firstBranchColumn; column <= header.length; column++) {
    const name = cellText_(header[column - 1]);
    if (!name) continue;
    const canonical = canonicalBranchName_(name);
    if (seen[canonical]) {
      throw new Error(`Summary 第 ${INVENTORY_SYNC.branchHeaderRow} 列有重複分館標題：${name}`);
    }
    seen[canonical] = true;
    branches.push({ name, column, canonical });
  }
  return branches;
}

function buildBranchPlan_(sheet, branchName, inventoryDate, summaryItems, branchValues) {
  const dateInfo = inspectDateColumn_(sheet, inventoryDate);
  const lastItemRow = findLastItemRow_(sheet);
  const itemCount = Math.max(0, lastItemRow - INVENTORY_SYNC.branchFirstDataRow + 1);
  const itemValues = itemCount
    ? sheet.getRange(INVENTORY_SYNC.branchFirstDataRow, 1, itemCount, 5).getValues()
    : [];
  const currentDateValues = dateInfo.column && itemCount
    ? sheet.getRange(INVENTORY_SYNC.branchFirstDataRow, dateInfo.column, itemCount, 1)
      .getValues()
      .map((row) => row[0])
    : Array(itemCount).fill(0);
  const branchItems = itemValues
    .map((row, index) => rowToItem_(row, INVENTORY_SYNC.branchFirstDataRow + index))
    .filter(Boolean);
  const maps = buildBranchMapsFromItems_(branchItems);
  const authorityKey2Counts = countKeys_(summaryItems.map((item) => item.key2));
  const authorityKey3 = toSet_(summaryItems.map((item) => item.key3));
  const dateValues = currentDateValues.slice();
  const targetOwners = {};
  const appendItems = [];
  const issues = [];

  summaryItems.forEach((item, index) => {
    const value = branchValues[index];
    const targetRows = findBranchRows_(item, maps, authorityKey2Counts);
    if (targetRows.length > 1) {
      if (value !== 0) {
        issues.push(
          `分館「${branchName}」品項不唯一：${item.key3}；` +
          '本次同步已停止，避免把庫存寫入錯誤列。'
        );
      } else {
        // 零庫存對所有同 key 的歷史重複列清為 0，不需要猜測哪一列是唯一目標。
        targetRows.forEach((rowNumber) => {
          dateValues[rowNumber - INVENTORY_SYNC.branchFirstDataRow] = 0;
        });
      }
      return;
    }
    if (targetRows.length === 0) {
      if (value !== 0) {
        appendItems.push({ item, value });
      }
      return;
    }
    const targetRow = targetRows[0];
    if (targetOwners[targetRow]) {
      issues.push(
        `分館「${branchName}」的品項 key 對應到同一列：${item.key3}；` +
        '請先整理分館頁籤重複品項。'
      );
      return;
    }
    targetOwners[targetRow] = item.key3;
    dateValues[targetRow - INVENTORY_SYNC.branchFirstDataRow] = value;
  });

  branchItems.forEach((item) => {
    const branchKey2Rows = maps.byKey2[item.key2] || [];
    const isCurrentAuthority = authorityKey2Counts[item.key2] === 1 && branchKey2Rows.length === 1
      ? true
      : Boolean(authorityKey3[item.key3]);
    if (!isCurrentAuthority) {
      dateValues[item.rowNumber - INVENTORY_SYNC.branchFirstDataRow] = 0;
    }
  });

  if (issues.length) throw new Error(issues.join('\n'));
  return {
    sheet,
    branchName,
    inventoryDate,
    dateInfo,
    lastItemRow,
    itemCount,
    dateValues,
    appendItems,
  };
}

function applyBranchPlan_(plan) {
  let dateColumn = plan.dateInfo.column;
  if (!dateColumn) {
    plan.sheet.insertColumnAfter(plan.dateInfo.insertAfter);
    dateColumn = plan.dateInfo.insertAfter + 1;
    plan.sheet.getRange(2, dateColumn).setValue(plan.inventoryDate).setNumberFormat('yyyy/m/d');
  }

  if (plan.itemCount) {
    plan.sheet.getRange(
      INVENTORY_SYNC.branchFirstDataRow,
      dateColumn,
      plan.itemCount,
      1
    ).setValues(plan.dateValues.map((value) => [value]));
  }

  if (!plan.appendItems.length) return;
  const firstNewRow = plan.lastItemRow + 1;
  plan.sheet.insertRowsAfter(plan.lastItemRow, plan.appendItems.length);
  if (plan.lastItemRow >= INVENTORY_SYNC.branchFirstDataRow) {
    const copyWidth = Math.max(INVENTORY_SYNC.firstHistoryColumn, dateColumn);
    plan.sheet.getRange(plan.lastItemRow, 1, 1, copyWidth).copyTo(
      plan.sheet.getRange(firstNewRow, 1, plan.appendItems.length, copyWidth),
      { formatOnly: true }
    );
  }
  plan.sheet.getRange(firstNewRow, 1, plan.appendItems.length, 5)
    .setValues(plan.appendItems.map((entry) => entry.item.baseValues));
  plan.sheet.getRange(firstNewRow, dateColumn, plan.appendItems.length, 1)
    .setValues(plan.appendItems.map((entry) => [entry.value]));
}

function inspectDateColumn_(sheet, inventoryDate) {
  const lastColumn = Math.max(sheet.getLastColumn(), INVENTORY_SYNC.firstHistoryColumn);
  const rowValues = sheet.getRange(2, 1, 1, lastColumn).getValues()[0];
  let lastDateColumn = INVENTORY_SYNC.firstHistoryColumn - 1;
  for (let column = INVENTORY_SYNC.firstHistoryColumn; column <= rowValues.length; column++) {
    if (sameDate_(rowValues[column - 1], inventoryDate)) return { column };
    if (rowValues[column - 1] instanceof Date) lastDateColumn = column;
  }
  return { column: null, insertAfter: lastDateColumn };
}

function findLastItemRow_(sheet) {
  const scanLastRow = Math.max(sheet.getLastRow(), INVENTORY_SYNC.branchFirstDataRow - 1);
  const scanCount = scanLastRow - INVENTORY_SYNC.branchFirstDataRow + 1;
  if (scanCount <= 0) return INVENTORY_SYNC.branchFirstDataRow - 1;
  const values = sheet.getRange(INVENTORY_SYNC.branchFirstDataRow, 1, scanCount, 3).getValues();
  let lastItemRow = INVENTORY_SYNC.branchFirstDataRow - 1;
  values.forEach((row, index) => {
    if (rowToItem_(row, INVENTORY_SYNC.branchFirstDataRow + index)) {
      lastItemRow = INVENTORY_SYNC.branchFirstDataRow + index;
    }
  });
  return lastItemRow;
}

function buildSummaryItems_(values) {
  const items = [];
  const seenKey3 = {};
  for (let row = INVENTORY_SYNC.firstDataRow - 1; row < values.length; row++) {
    const item = rowToItem_(values[row], row + 1);
    if (!item) continue;
    if (seenKey3[item.key3]) throw new Error(`Summary 有重複品項：${item.key3}`);
    seenKey3[item.key3] = true;
    items.push(item);
  }
  const key2Counts = countKeys_(items.map((item) => item.key2));
  items.forEach((item) => { item.key2Count = key2Counts[item.key2]; });
  return items;
}

function buildBranchMaps_(sheet) {
  const lastItemRow = findLastItemRow_(sheet);
  if (lastItemRow < INVENTORY_SYNC.branchFirstDataRow) {
    return { byKey2: {}, byKey3: {}, items: [] };
  }
  const values = sheet.getRange(
    INVENTORY_SYNC.branchFirstDataRow,
    1,
    lastItemRow - INVENTORY_SYNC.branchFirstDataRow + 1,
    5
  ).getValues();
  const items = values
    .map((row, index) => rowToItem_(row, INVENTORY_SYNC.branchFirstDataRow + index))
    .filter(Boolean);
  return buildBranchMapsFromItems_(items);
}

function buildBranchMapsFromItems_(items) {
  const byKey2 = {};
  const byKey3 = {};
  items.forEach((item) => {
    pushMap_(byKey2, item.key2, item.rowNumber);
    pushMap_(byKey3, item.key3, item.rowNumber);
  });
  return { byKey2, byKey3, items };
}

function findBranchRows_(item, maps, authorityKey2Counts) {
  const key2Rows = maps.byKey2[item.key2] || [];
  // 先用凱惠料號 + 品名；若分館頁籤該 key 不唯一，再用完整 key3。
  // 這保留既有 learning 的 fallback 規則，也避免同一品名重複列誤寫。
  if (authorityKey2Counts[item.key2] === 1 && key2Rows.length === 1) return key2Rows;
  return maps.byKey3[item.key3] || [];
}

function findBranchRow_(item, maps, authorityKey2Counts) {
  const rows = findBranchRows_(item, maps, authorityKey2Counts);
  return rows.length === 1 ? rows[0] : null;
}

function rowToItem_(row, rowNumber) {
  const itemNo = cellText_(row[0]);
  const itemCode = cellText_(row[1]);
  const itemName = cellText_(row[2]);
  if (!itemCode || itemCode === '#N/A' || !itemName) return null;
  return {
    rowNumber,
    rowIndex: rowNumber - 1,
    itemNo,
    itemCode,
    itemName,
    key2: `${itemCode}||${normalize_(itemName)}`,
    key3: `${itemNo}||${itemCode}||${normalize_(itemName)}`,
    baseValues: row.slice(0, 5),
  };
}

function resolveBranchSheet_(sheetByName, branchName) {
  const normalized = normalize_(branchName);
  if (sheetByName.exact[normalized]) return sheetByName.exact[normalized];
  const canonical = canonicalBranchName_(branchName);
  return sheetByName.branch[canonical] || null;
}

function canonicalBranchName_(branchName) {
  const normalized = normalize_(branchName);
  const canonicalNames = Object.keys(INVENTORY_SYNC.branchSheetAliases);
  for (let index = 0; index < canonicalNames.length; index++) {
    const canonical = canonicalNames[index];
    if (normalize_(canonical) === normalized) return canonical;
    if (INVENTORY_SYNC.branchSheetAliases[canonical].some((alias) => normalize_(alias) === normalized)) {
      return canonical;
    }
  }
  return normalized;
}

function visibleSheetNames_(ss) {
  return ss.getSheets().map((sheet) => sheet.getName()).join('、');
}

function debugInventorySheetNames() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  SpreadsheetApp.getUi().alert(visibleSheetNames_(ss));
}

function buildNormalizedSheetMap_(ss) {
  const exact = {};
  const branch = {};
  ss.getSheets().forEach((sheet) => {
    const normalized = normalize_(sheet.getName());
    if (exact[normalized]) {
      throw new Error(`頁籤名稱正規化後重複：${sheet.getName()} 與 ${exact[normalized].getName()}`);
    }
    exact[normalized] = sheet;

    const canonical = canonicalBranchName_(sheet.getName());
    if (!INVENTORY_SYNC.branchSheetAliases[canonical]) return;
    if (branch[canonical]) {
      throw new Error(
        `分館頁籤別名重複：${branch[canonical].getName()} 與 ${sheet.getName()} 都對應 ${canonical}`
      );
    }
    branch[canonical] = sheet;
  });
  return { exact, branch };
}

function pushMap_(map, key, rowNumber) {
  if (!map[key]) map[key] = [];
  map[key].push(rowNumber);
}

function countKeys_(keys) {
  const counts = {};
  keys.forEach((key) => { counts[key] = (counts[key] || 0) + 1; });
  return counts;
}

function toSet_(keys) {
  const result = {};
  keys.forEach((key) => { result[key] = true; });
  return result;
}

function requireSheet_(ss, name) {
  const sheet = ss.getSheetByName(name);
  if (!sheet) throw new Error(`找不到頁籤：${name}`);
  return sheet;
}

function normalize_(value) {
  return String(value || '').replace(/[\s　]+/g, '').trim();
}

function cellText_(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'number' && Number.isInteger(value)) return String(value);
  return String(value).trim();
}

function hasInput_(value) {
  return value !== null && value !== undefined && String(value).trim() !== '';
}

function toNumber_(value) {
  if (!hasInput_(value)) return 0;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error(`庫存值不是有限數字：${value}`);
    return value;
  }
  const text = String(value).replace(/,/g, '').trim();
  if (!text) return 0;
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) throw new Error(`庫存值不是有限數字：${value}`);
  return parsed;
}

function sameDate_(a, b) {
  return a instanceof Date &&
    b instanceof Date &&
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
}
