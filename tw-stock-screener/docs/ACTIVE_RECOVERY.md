# 原KD補資料與重算：前300自主流程

## 最新工作：本機試用第一版

使用者已確認「第一版照建議」並要求繼續，授權先檢查30檔，通過後自動完成同一交易日前300候選。SPEC第11節取代原30檔停止限制。

前30檔已於11:29完成，耗時約24.6分鐘；27檔日KD、22檔週KD、6檔月KD通過，最終1檔6504南六通過量比。10檔條件不符，19檔資料不足。完整離線重播一致，仍為validation_only。

0.3.0工作於13:06啟動；13:13第38檔受控中斷後，13:15最終程式由同一份月份快取接續。初查前37檔快速重算，沒有重抓已完整月份。兩個Python版本各107項測試通過。此處啟動與檢查紀錄不是宣稱工作目前必定仍在執行。

```powershell
# 目前入口：只看狀態，不下載
.\.venv\Scripts\python.exe -m tw_stock_screener --config local.yaml work-status
```

最新工作目錄由`output/work-current.json`指向；程序會自行完成全候選、離線核對、診斷及待確認報告。操作見[FIRST_VERSION.md](FIRST_VERSION.md)。第一版自動流程未完成前，不應另開舊recover_run或screen下載。沒有GitHub提交、正式結果確認或排程。

## 前30檔工作的歷史紀錄（2026-10-11 11:04啟動）

當時使用者確認先驗證原排名前30檔，完成即停止。此限制後來由第一版授權取代。更早一份300檔工作已於10:39失敗，不能依下方歷史啟動紀錄判斷仍在執行。

- 目前入口：本工作區`tools/recover_run.py --limit 30`。
- 行情副本：沿用`data/recovery-20261011-102003/market.sqlite3`，包含先前已補資料；不重建、不覆蓋原D槽資料庫。
- 目前工作目錄：`data/recovery-20261011-102003/validation-30/`。
- 啟動器PID7032、實際Python PID13008；初查已到第13／30檔且開始補缺。動態狀態以該目錄run-state.json及程序存活為準。
- 啟動資訊及標準輸出／錯誤路徑：`output/sample30-current.json`。
- 樣本只匯出validation-30 JSON／CSV，publication_status=validation_only；不寫入每日結果版本庫、不批准正式結果。
- 完成後自動離線重播。`verification.json`列出逐項一致性、樣本資料錯誤及全市場排名錯誤；`summary.json`為該次完成摘要。重跑開始後舊summary仍是舊紀錄，須核對run-state及輸出時間，不可將舊預檢誤當本次完成。
- 本次修改前程式及行情副本備份在工作區`.tmp/sample30-before-20261011-105947/`；94項測試通過，詳見`test-results-sample30.txt`。

```powershell
# 在目前tw-stock-screener目錄查看；不啟動第二份工作
Get-Content .\data\recovery-20261011-102003\validation-30\run-state.json
Get-Content .\data\recovery-20261011-102003\validation-30\run.log -Tail 15 -Wait
```

開始前的真實離線預檢只花約8秒，沒有HTTP請求，處理30檔並可重現；其中9檔仍缺後續階段月份，另12檔有OHLC、缺交易日或暖機問題，故不能視為下載完成或正式清單。預檢報告保留於`validation-30/output/validation-30-2026-10-08-20261011T030413467437Z.json`。

另以唯讀行情快取及獨立Decimal遞迴公式核對預檢已算出的51組日／週／月KD，最大絕對差4.27×10^-14；5筆已算量比及300筆漲幅百分比亦一致（漲幅最大差1.78×10^-15）。核對自行分組高低收盤，沒有呼叫核心KD或聚合函式。證據見[sample30-independent-math-preflight.json](sample30-independent-math-preflight.json)。這只驗證現有資料的數學一致性，不能驗證缺失歷史、官方資料內容正確性或看盤軟體口徑，也不是線上補缺後的最終結果。

## 前一份300檔工作的歷史紀錄

啟動時間：2026-10-11 10:20（Asia/Taipei）。本頁記錄啟動事件，動態狀態以run-state.json為準，不代表程式永遠仍在執行。

- 使用者要求：前述本機備份、統一執行版本、重用快取補資料及重算立即啟動。
- 執行版本：本工作區修正版，入口`tools/recover_run.py`；未覆寫D槽舊程式。
- 原行情SQLite：`D:\code\1010_k線\data\market.sqlite3`，以SQLite backup唯讀複製至本次工作目錄，所有行情資料表更新只寫副本。
- 原始官方回應：重用D槽raw與requests.jsonl，解壓後確認SHA256，缺少或損毀才重新請求官方API。新回應寫入分層資料夾；不搬移或刪除舊檔。
- 目標日：2026-10-08；沿用原2026-10-10分類快照及current_universe警語。這是原執行的修復重算，不能當作同日名單的嚴格歷史回測。原截止日後已記錄的休市狀態直接重用，未記錄的後續日期禁止猜測或請求行情。
- 不變事項：原selection、kd、history設定與目前config逐項相等才啟動；沿用application及screener，不複製選股條件。完成後檢查原前300排名與目標日一致，否則停止保存成功版本。
- 結果：僅新增待確認版本，正式結果不變；沒有GitHub提交、部署或排程。八法程式未修改。

## 本次工作目錄

`data/recovery-20261011-102003/`

| 檔案 | 用途 |
| --- | --- |
| market.sqlite3 | 複製原快取，增量補資料，保留作為後續續跑快取 |
| universe.json、recovery.json | 保留原名單及原報告／名單雜湊、分類警語與原執行參考日期 |
| effective-config.json | 本次有效設定及所有路徑 |
| inventory.json | 各階段條件式缺漏上限，不能相加當實際下載量或完成時間 |
| run-state.json | 程序PID、running／complete／failed、候選進度及下載／原始回應重用數 |
| run.log | 完整過程與錯誤 |
| summary.json | 完成後才產生，含版本、階段數量、錯誤數與輸出路徑 |
| output/screen-*.json、*.csv | 完成後的待確認報告，不覆蓋舊報告 |
| failure-*.json | 整體失敗時保存原因 |

啟動器PID8576、實際Python PID13804，屬同一份工作。初次檢查已進入第2／300個候選、重用1份原始回應並開始真實網路請求；這是當時進度，不是現在進度。啟動資訊亦存於`output/recovery-current.json`。

## 查看與續跑

保持電腦開機且不進入睡眠。程式在本機執行；本次沒有建立完成後主動通知的排程。

```powershell
# 在目前tw-stock-screener目錄查看，這不會啟動第二份工作
Get-Content .\data\recovery-20261011-102003\run-state.json
Get-Content .\data\recovery-20261011-102003\run.log -Tail 15 -Wait
```

只有確認原程序已結束／失敗後，才能用同一工作目錄續跑：

```powershell
.\.venv\Scripts\python.exe -u tools/recover_run.py --original-report D:\code\1010_k線\output\screen-2026-10-08-20261010T165526054750Z.json --universe D:\code\1010_k線\data\universe-2026-10-10.json --run-dir data/recovery-20261011-102003 --limit 30
```

續跑會檢查原輸入雜湊及已保存名單，沿用本次副本中已補齊的月份；不重新複製原DB覆蓋進度。不要在此工作仍執行時再次使用這條命令。

上述續跑指令已依使用者最新授權加上`--limit 30`。原300檔工作停在第12檔進度JSON替換WinError5，並沒有完成第12檔；舊run-state及failure檔保留於根恢復目錄。此次修復只讓進度寫入問題不再中斷計算，必要資料存檔錯誤仍停止。

新入口4項單元測試已在啟動前通過，驗證原始回應重用、損毀或錯誤回應重抓、截止日後不得猜測交易日、已記錄休市不得請求後續行情。既有82項測試的舊紀錄保留；完整測試與實際重算結果分開記錄。

啟動後完整執行本工作區Python3.12的86項單元測試通過，紀錄見[test-results-resume.txt](test-results-resume.txt)。10:21檢查實際重算仍在第2個候選的月KD資料取得階段，已發出50次必要網路請求；候選停留同一檔不等於程序停止。程序未完成，不能將86項測試通過當作本次市場結果驗收。
