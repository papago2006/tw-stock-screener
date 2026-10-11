# tw-stock-screener

台灣上市、上櫃普通股每日選股。第一階段包含官方來源、按需增量快取、KD、選股核心、CLI、JSON/CSV 與測試；不包含網頁報表。

## 最新狀態：完整工作完成，手機發布已授權

2026-10-11台灣時間16:10完成原2026-10-08前300候選：入選9、條件不符136、資料不足155；另有36筆排名前問題。最終離線重播一致；結果仍為待確認版本，外部看盤軟體KD未驗收。

手機靜態報表原始檔位於`web/`，`web_export`只讀已保存版本及核對摘要，不連網、不計算、不批准。`site/`是公開欄位輸出；使用者已明確授權將程式及待確認試算快照上傳至公開的papago2006/tw-stock-screener並部署Pages，目前正在處理。詳見[發布計畫](docs/DEPLOYMENT.md)，包含重建指令、公開範圍、GitHub根目錄及後續雲端工作。第一階段不開發報表的原始範圍保留為歷史說明，目前進度以本節及SPEC第14節為準。

## v0.3：本機試用第一版

使用者已確認KD本機工具優先；先核對原30檔，通過後自動處理同一日期完整前300候選，再離線核對、產出待確認結果及中文報告。選股條件不變，外部看盤軟體KD比對尚待驗收。手機入口與雲端整合保留下一階段。

- 雙擊`Run-First-Version.cmd`：執行／續跑原2026-10-08第一版工作。
- 雙擊`Check-Progress.cmd`：只看最新進度，不啟動下載。
- 雙擊`Run-Screener.cmd`：執行最新共同交易日選股。

目前第一版工作已於2026-10-11 13:06啟動，不必重複啟動。操作、有效設定檔、工作輸出及續跑／重算方式見[第一版操作說明](docs/FIRST_VERSION.md)。一鍵入口使用完整本機設定`local.yaml`；`config.yaml`為另外的通用範例，兩份不自動合併。新入口work具備共享快取鎖及有限失敗預算，不能與舊下載入口並行操作相同快取。

本次Python3.12及3.13各107項測試通過，見[3.12測試](docs/test-results-v1.txt)及[3.13測試](docs/test-results-v1-python313.txt)。既有真實30檔在提前排除最佳化後，排名、各階段數量、入選、已計算K/D及量比均一致且0次HTTP，見[v1-sample-regression.json](docs/v1-sample-regression.json)。19檔診斷中的183個問題日期都可由已保存官方回應驗證，另1檔暖機不足，見[first-version-diagnosis.json](docs/first-version-diagnosis.json)。13:13在第38檔進行受控中斷，13:15新版接續並重用已完成月份，見[實際續跑證據](docs/v1-resume-check.json)。這些不是完整300檔結果或外部軟體驗收；完整工作完成狀態請查work-status。

以下v0.2及早期來源修復段落保留為歷史說明；目前範圍以SPEC第11節與第一版操作說明為準。

## v0.2：清楚分工、結果版本與追蹤

選股條件與日週月歷史窗口保持原樣。工作流程移至 `application.py`，KD 驗證移至 `verification.py`；新增 `results.py` 與 `tracking.py`。正式規格見 [SPEC](docs/SPEC.md)，模組及擴充方式見 [ARCHITECTURE](docs/ARCHITECTURE.md)，需求沿革見 [CHANGELOG](docs/CHANGELOG.md)。

每次 `screen` 都產生待確認版本，保存於 YAML `paths.results` 指定的獨立 SQLite 資料庫；原 JSON／CSV 仍保留。計算完成不代表正式發布，重算也不會取代正式結果。

```powershell
# 完成選股後，取得輸出中的 version ID
python -m tw_stock_screener screen
python -m tw_stock_screener versions --date 2026-10-08
python -m tw_stock_screener show-result --version <新版本ID> --output output/review.json

# 檢閱結果後，首次確認該日正式版
python -m tw_stock_screener approve --version <新版本ID> --expected-current none

# 重算後先查看舊正式版，再明確確認新版本
python -m tw_stock_screener show-result --date 2026-10-08
python -m tw_stock_screener approve --version <新版本ID> --expected-current <舊正式版本ID>

# 比較前一交易日正式結果；缺歷史會明確標示
python -m tw_stock_screener track --date 2026-10-08 --output output/tracking.json
```

上面的 `<...>` 是需替換的版本 ID，不要原樣貼進 PowerShell。含錯誤的版本預設不能確認；檢閱後若仍要確認須明確加 `--allow-errors`，追蹤仍標為資料不完整，不產生正常新增／退出訊號。使用者已確認首次及重算全部需明確確認才成為正式結果，不自動正式化。

版本查詢、確認與追蹤不下載行情，不建立行情快取。連續天數若缺更早正式結果，只列可確認下限（`exact: false`）；規則不同也不直接比較。舊版輸出不自動核准或匯入。

本次修改在「1010_k線 2/tw-stock-screener」工作區；D 槽仍為先前版本，正在執行的下載保持原狀。Actions、Pages、手機觸發、ChatGPT 查詢及完整行情修訂封存仍未實作；KD 比對軟體尚未選定。

本次 Python 3.12.14 與 3.13.5 各69項測試通過，包含正式版本切換、舊版本保留、過期確認拒絕、缺前日結果、錯誤結果、規則不同、連續天數與指標差異；記錄見 [3.12](docs/test-results-architecture.txt) 與 [3.13](docs/test-results-architecture-python313.txt)。這些測試沒有代替完整市場選股或看盤軟體驗收。

## 本次修改：需要資料時才下載

`screen` 不再執行十年全市場回填。流程如下：

1. 查詢指定日／最新共同交易日的上市、上櫃行情，以官方 ISIN CFI 分類識別普通股。
2. 依漲跌幅降冪排名，取前 300，不遞補。
3. 只為前 300 載入日 KD 所需的個股歷史；不通過就停止該股後續下載。
4. 日 KD 通過才擴充週 KD 歷史；週 KD 通過才擴充月 KD 歷史。
5. 三個 KD 都通過，才取得前一交易日的官方日報精確成交股數，計算量比。

每一步先查本地快取，只抓缺少的個股月份。共享的官方交易日曆也有快取，用於檢查資料缺日。重複執行同一日期，已完整取得的月份不會重抓；當月快取只覆蓋至上次查詢截止日，查詢較晚日期才更新。歷史回測即使讀到較新快取，也會先截斷至選股日。

既有全市場快取保留，完整月份可直接供新版使用；個股月份快取與全市場日快取使用不同資料表，不會把一檔股票的下載標成全市場完成。

首次仍可能需要較多請求，耗時取決於實際通過各階段的股票數與官方服務速度，**不保證數秒完成**。預設序列請求間隔 2 秒，不會為了縮短時間改用模擬行情、平行轟炸或降低暖機要求。`sync` 仍保留為手動全市場同步工具，但 `screen` 不會呼叫它。

## 安裝與執行

需要 Python 3.11 以上，唯一第三方依賴為 PyYAML。Windows PowerShell，在包含 `pyproject.toml` 的目錄執行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m tw_stock_screener screen
```

`D:\code\1010_k線` 已安裝虛擬環境，不必重新建立。Linux/macOS 可用 `python3 -m venv .venv` 與 `.venv/bin/python -m pip install -e .`。

以下 `python` 表示已安裝套件的 Python；Windows 可替換成 `.\.venv\Scripts\python.exe`：

```powershell
# 最新共同交易日，使用當天官方分類快照
python -m tw_stock_screener --config config.yaml screen

# 指定歷史交易日，必須提供該日官方分類快照
python -m tw_stock_screener screen --date 2025-09-30 --universe data/universe-2025-09-30.json

# 必要快取完整後，可離線重播
python -m tw_stock_screener screen --date 2025-09-30 --universe data/universe-2025-09-30.json --offline

# 重抓本次實際用到的資料，供官方資料修正後使用
python -m tw_stock_screener screen --refresh

# 保存當下官方分類
python -m tw_stock_screener snapshot-universe --output data/universe-current.json

# 明確要求才執行全市場日期範圍同步
python -m tw_stock_screener sync --start 2025-09-29 --end 2025-09-30

# 查看執行進度；Ctrl+C 只結束查看
Get-Content .\output\run.log -Tail 20 -Wait
```

`--config` 放在子命令之前，設定中的路徑相對於 YAML 所在目錄。指定休市日報錯，不默默改日期；最新模式只在兩市場都明確無資料時往前搜尋。HTTP、JSON、schema 或兩市場日期不一致不會被當作休市。所有日期採台北時區，不接受未來日期。

## 選股與資料定義

- 普通股：官方 ISIN CFI 前兩碼 `ES`，排除 ETF、權證、特別股、TDR；不是單靠四位代號判斷。
- 漲跌幅：官方有號漲跌價差 ÷（收盤價 − 官方有號漲跌價差）× 100。這是官方參考基準漲跌幅，除權息日不一定等於未還原前日收盤報酬。`X`、`除權/除息/---` 等無法識別基準的資料記錯並排除，不自填為零。
- 合併兩市場後排名前 300；同漲幅依代號、再市場排序。排名後不遞補，資料覆蓋率見 `universe/quoted/rankable` 計數與錯誤。
- 依序：日 K>D → 週 K>D → 月 K>D → 當日／前一市場交易日成交股數 ≥3.2。沒有最低成交量限制；K=D 不通過。
- 價格一律未還原，先截斷到選股日，再聚合日／週／月。週採 ISO 週，月採曆月，包含選股日所在未完成棒；首筆 Open、最大 High、最小 Low、末筆 Close。

```text
HH = 最近 26 根棒最高價的最大值
LL = 最近 26 根棒最低價的最小值
RSV_t = 100 × (Close_t − LL) / (HH − LL)
K_t = (2 × K_(t−1) + RSV_t) / 3
D_t = (2 × D_(t−1) + K_t) / 3
初始 K = D = 50
```

前 25 根沒有 RSV/K/D，第 26 根開始遞迴，中間值不四捨五入。預設各尺度至少需要 26+60=86 根棒；HH=LL 時記錯排除，不任選 RSV。缺必要價格、缺交易日、重複行情、暖機不足、前日零量或 API 失敗都不能通過。各階段會檢查歷史中的選股日 OHLC 是否與排名日報一致。

### 暖機範圍

`config.yaml` 的 `history.daily_months: 6`、`weekly_months: 24`、`months: 120` 分別控制日、週、月階段的每日價格範圍；起點取往前指定月份的月初，較晚掛牌者自掛牌日起。每階段仍驗證至少 86 根該尺度 K 棒，範圍不足不會自動降低要求。

**與舊版的差異**：舊版日、週、月都從十年前初始化；新版日、週以各自窗口初始化，所以可能有微小數值差異，接近 K=D 時也可能影響結果。公式、50 種子、暖機與選股門檻不變。若需要沿用舊版十年初始化，可把 `daily_months` 與 `weekly_months` 也設為 120；仍然只為前 300 與各階段通過股票按需下載。

JSON 保存各階段實際 `*_history_start` 以及 `history_months`，方便重現計算。

### 成交量精度

TWSE 個股月查詢含精確成交股數；TPEx 個股月查詢只有成交張數，且統計範圍與日報可能不同。因此 TPEx 歷史月查詢只取 OHLC，成交量保留 `null`，不乘 1,000 冒充精確股數，也不補零。KD 只需要 OHLC；最終量比一律讀取目標日與前一市場交易日的官方每日全市場日報精確成交股數。精確成交量缺漏即排除。

## 官方來源與分類限制

- TWSE 日報：`exchangeReport/MI_INDEX`。
- TPEx 日報：`www/zh-tw/afterTrading/dailyQuotes`。
- TWSE 個股歷史：`exchangeReport/STOCK_DAY`，每次一檔一個月。
- TPEx 個股歷史：`www/zh-tw/afterTrading/tradingStock`，每次一檔一個月。
- 共用交易日曆：TWSE `exchangeReport/FMTQIK`，每次一個月；假設兩市場共同開市，若個股缺日、或目標／前日兩市場日報不一致則保守排除／停止。
- 普通股分類：官方 ISIN `strMode=2` 與 `strMode=4`。

官方回應與驗證見 [來源驗證](docs/SOURCE_VALIDATION.md)。來源可能限流、403、逾時或改版；失敗會留紀錄，不用非官方替代行情。原始回應以 gzip 保存，附網址、SHA-256、取得時間。新檔分層存於 `raw/v2/雜湊前兩碼/SHA256.gz`，舊平面檔案保留並相容，避免 FAT32 單一資料夾項目耗盡。`requests.jsonl` 新增 `raw_path`，亦可用 `sources.raw_response_path` 定位新舊檔。存檔失敗會停止執行，與 API 重試分開。Python 3.13 特定憑證格式問題的相容重試仍驗證 CA 與主機名稱。

2026-10-11 修復中文終端輸出、TPEx 舊月份欄位與掛牌日日期註記；未更改選股條件。原 D 槽舊執行有 261 筆錯誤，7 檔暫存結果仍未驗收。本次兩個 Python 版本各 82 項測試通過，單一原失敗官方請求在 D 槽分層存檔成功；完整市場尚未重算。修正位於本工作區，未覆寫 D 槽舊版程式。詳見[檢查與修復紀錄](docs/RECOVERY_20261011.md)。

官方 ISIN 只有本次已驗證的當下分類名單，沒有找到可任意指定歷史日期的分類端點。歷史模式要求 `as_of` 等於選股日的完整官方快照，以避免今天名單造成存活者偏差。最新模式採當天分類，記錄日期及警語，不宣稱前一交易日的嚴格歷史名單完整。

若保存過當日兩市場官方 CP950 HTML，可轉換：

```powershell
python -m tw_stock_screener import-universe --twse-html archives/twse.html --tpex-html archives/tpex.html --output data/universe-2025-09-30.json
```

轉換會從 HTML 取得日期、CFI 與掛牌日，兩市場更新日期須相同。匯入者須保證資料完整與來源真實，程式檢查不是官方數位簽章。

## 輸出、快取與測試

2026-10-11目前先驗證原2026-10-08排名前30檔，沿用先前重算已補齊的行情，完成後不自動擴大。一般`screen`的前300選股條件不變。工作狀態、日誌、續跑指令及先前中斷原因見[目前驗證工作](docs/ACTIVE_RECOVERY.md)；已有程序執行時不要再啟動一份。

樣本輸出檔名為`validation-30-*`，只供驗證，不進入每日結果版本庫；完成後自動離線重播並保存`verification.json`。單元測試、可重現的樣本、完整市場驗收及外部KD軟體比對是不同項目，不能互相替代。本次94項測試紀錄見[test-results-sample30.txt](docs/test-results-sample30.txt)。

成功輸出唯一檔名的 `output/screen-日期-時間.json`、`.csv`；保留原結果。

- JSON：選股日、前日、分類快照、設定、資料基準、各階段數量、通過股票、前 300 逐股結果與拒絕階段、錯誤原因、各階段歷史起點。
- CSV：代號、名稱、市場、漲幅、成交股數、量比與日週月 K/D；UTF-8 BOM，代號請以文字匯入 Excel。
- 執行失敗：回傳碼 2，寫 `failure-*.json` 與 `run.log`，不輸出成功名單；個股資料錯誤則留在成功報告的 `errors`。
- SQLite：舊 `days/quotes` 全市場快取保留；新增 `stock_months` 個股月份及 `calendar_months` 日曆覆蓋紀錄。未完成月不會永久視為完整，失敗月份不標記成功。
- 磁碟保留至少 1 GiB，空間不足會停止，釋放空間後可續跑。

```powershell
python -m unittest discover -s tests -v

# 讀取既有日／月快取，匯出逐棒 OHLC、RSV、K、D
python -m tw_stock_screener verify-kd --date 2026-10-08 --market TWSE --code 2330 --timeframe daily --output output/kd.csv

# 比較外部參考 CSV（date,k,d），需相同資料起點、種子、視窗與未還原價格
python -m tw_stock_screener verify-kd --date 2026-10-08 --market TWSE --code 2330 --output output/kd.csv --reference reference.csv --tolerance 1e-8
```

驗證工具是低階數值比對，不代表完整行情或選股通過。TPEx 月查詢中的未知成交量在驗證輸出仍為空值。

按需測試包含：前 300 外不抓歷史、日失敗不抓週／月、週失敗不抓月、三個 KD 通過才抓精確前日量、重疊月份重用、部分月更新、歷史截止日、缺日排除，以及 CLI 不呼叫全市場 `sync`。測試紀錄見 [test-results-demand.txt](docs/test-results-demand.txt)。測試合成行情只用於測試，不冒充實際名單。

## 模組與尚未解決問題

`sources.py` 官方來源與解析、`cache.py` 全市場日快取、`demand.py` 按需月份／日曆快取、`indicators.py` 聚合與 KD、`screener.py` 純選股核心及階段介面、`cli.py` 執行流程，另有設定、模型與輸出模組。資料來源與儲存分離，便於未來 Actions。

仍需注意：歷史分類快照須自行提供；轉板、改代號及跨市場歷史尚未銜接；特殊價差無法識別基準者排除；停牌或歷史缺 OHLC 者保守排除；2010 年前資料未接官方舊檔。快取保留原始資料但沒有完整修訂版本管理。尚未整合 GitHub Actions、Pages、手機報表或 ChatGPT 查詢。
