# 本機試用第一版操作說明

程式版本0.3.0。使用者已確認先完成原KD工具；八法維持獨立專案，手機與雲端整合稍後接續。外部看盤軟體KD比對仍待驗收。

## 現在怎麼操作

在本專案資料夾可直接雙擊以下檔案：

| 檔案 | 用途 |
| --- | --- |
| Run-First-Version.cmd | 執行／續跑原2026-10-08的第一版工作。先核對前30檔，再自動接續前300、離線核對及產出待確認結果。工作已完成時直接顯示原摘要；不重抓、不另建版本。 |
| Check-Progress.cmd | 查最新工作進度、目前階段、候選數及結果位置；不啟動選股、不下載行情。 |
| Run-Screener.cmd | 對最新共同交易日建立工作，使用當天官方分類快照；首次資料取得可能耗時，官方來源不可用時明確失敗。 |

目前已於2026-10-11台灣時間13:06啟動第一版原日期工作，使用者無須再啟動。動態狀態以`output/work-current.json`指向的工作目錄為準。不要同時執行舊screen／recover_run下載入口。

13:13在第38檔進行受控中斷檢查，確認OS鎖會釋放且狀態查詢能辨識中斷；13:15最終0.3.0程式接續執行。已完成月份均保留，前37檔從快取重新計算，只有中斷時未完成月份需再請求。詳見`v1-resume-check.json`；舊工作與日誌保留。Python3.12／3.13各107項測試通過，0.3.0套件已在本工作區重新安裝。

一鍵入口採`local.yaml`，它是本機完整設定檔，包含選股參數及快取位置。`config.yaml`保留為另一份通用設定範例，兩份不自動合併。修改前先對照SPEC；更改規則會產生不同工作識別，不能以修改後規則冒充原執行續跑。

本機需保持開機且避免睡眠。進度與月份快取會保存；程序中斷後再次執行同一命令，可重用已取得的月份。續跑會重新計算已處理股票的指標，通常很快，不宣稱可直接跳過一切計算。沒有啟用每日排程、雲端常駐服務或手機通知。

## 第一版會自行完成哪些事

1. 取得共享快取的作業鎖；已有相同入口的工作執行中則拒絕第二份工作。
2. 驗證原報告、分類快照及設定；不覆寫D槽舊程式／原行情資料庫。
3. 先處理前30檔，完全離線重播，檢查結果、錯誤及計算輸入雜湊。
4. 診斷資料問題並保存確切日期與可驗證的官方原始回應證據。
5. 通過後處理完整前300候選。優先檢查已完整快取的月份；若已知缺日或價格無效即可排除，停止下載該股無關月份。這是提前執行既有排除條件，不放寬選股規則。
6. 每個必要月份先查SQLite及雜湊驗證過的原始回應，缺少才請求官方來源。HTTP重試沿用network.attempts，不另疊加重試倍數。耗盡的個股月份失敗另存工作紀錄，不能標成行情已完整。
7. 完整計算後再次離線重播。個股網路失敗可由獨立失敗紀錄重現；離線驗證不得HTTP、不得補資料。
8. 核對通過才新增待確認結果版本，匯出JSON、CSV、中文報告與資料診斷；不自動批准正式結果。

交易日或原排名不一致、離線核對不一致、無法辨識的重要資料結構、必要資料存檔失敗會停止並保存失敗原因。暫時個股API失敗經有限重試後記錄資料不足，其餘股票可繼續。磁碟不足不以個股正常排除掩蓋。

## 工作輸出

`output/work-current.json`記錄最新工作目錄，例如`output/jobs/<工作ID>/`：

| 檔案 | 說明 |
| --- | --- |
| identity.json、effective-config.json | 工作條件、程式雜湊、原輸入及有效設定 |
| run-state.json、run.log | 動態進度與詳細紀錄；進度JSON寫入暫時失敗不影響必要資料保存 |
| sample.json、sample-verification.json、sample-diagnostics.json | 前30檔檢查及證據，不能作正式每日結果 |
| calculation.json | 完整計算的中間保存，不等於已通過核對或正式結果 |
| verification.json | 完整離線核對及禁止HTTP的檢查 |
| diagnostics.json | 個股問題日期、原始回應雜湊／路徑及未解原因；排名資料問題另外列出 |
| report.md | 中文摘要及每個候選的入選／條件不符／資料不足狀態 |
| output/screen-*.json、*.csv | 唯一時間戳輸出；JSON含日週月K/D、量比、來源及版本資訊 |
| summary.json | 全部步驟完成後的摘要與待確認版本ID |
| request-failures.json、failure-*.json | 耗盡的來源請求及整體中斷原因，不冒充完整行情快取 |

`complete_with_exclusions`表示流程完成且仍有資料不足排除，不代表全市場資料或KD軟體已完整驗收。舊資料與報告均保留。

## 指令與重新計算

在包含pyproject.toml的本專案資料夾執行：

```powershell
# 原日期第一版：執行或沿快取續跑
.\.venv\Scripts\python.exe -m tw_stock_screener --config local.yaml work --recorded-run data/recovery-20261011-102003

# 查看進度，不下載
.\.venv\Scripts\python.exe -m tw_stock_screener --config local.yaml work-status

# 額外輸出可供其他工具讀取的JSON
.\.venv\Scripts\python.exe -m tw_stock_screener --config local.yaml work-status --json

# 最新共同交易日
.\.venv\Scripts\python.exe -m tw_stock_screener --config local.yaml work

# 已完成後另建工作及待確認版本；沿用快取，重新給定來源請求預算
.\.venv\Scripts\python.exe -m tw_stock_screener --config local.yaml work --recorded-run data/recovery-20261011-102003 --new-run

# 嚴格指定歷史日：仍須該日官方分類快照
.\.venv\Scripts\python.exe -m tw_stock_screener --config local.yaml work --date 2025-09-30 --universe data/universe-2025-09-30.json

# 測試不下載行情；其中合成資料只用於測試
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

一般work以當日、設定、程式雜湊及輸入識別同一份工作，完成後重複命令不自動建立第二個版本。要重新計算請使用new-run。原日期恢復模式保留2026-10-10分類快照的current_universe警語，不是同日分類名單的嚴格歷史回測。

查看待確認版本及正式確認仍用既有versions／show-result／approve。含錯誤的結果預設禁止確認；明確檢閱後的allow-errors不會讓跨日追蹤假裝資料完整。這次沒有批准任何具體結果。

## 安裝

本工作區已具備可執行的.venv。移至另一台電腦需安裝Python3.11以上，並調整local.yaml路徑、帶入合法取得的行情快取或讓程式按需取得；不能只複製舊虛擬環境。

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

第一版不包含GitHub提交、排程、手機入口、完整行情修訂封存、跨市場改代號銜接及外部KD軟體驗收。
