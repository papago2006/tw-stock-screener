# 架構與擴充方式

版本：0.2.0。先讀 SPEC；本文說明模組責任，不新增選股條件。

## 靜態手機報表與發布界線

`web_export`為獨立呈現介面，以SQLite只讀連線取得已保存版本，驗證報告雜湊及對應工作摘要；只有完整、已離線核對的結果可匯出。正式／待確認狀態從結果庫讀取，匯出不批准。公開資料用明確欄位清單，不帶本機路徑、完整設定或原始例外；排除原因使用安全分類。`web/`只讀這份公開資料，不另算KD、排名或量比；`site/`為生成檔。不同版本需用新輸出目錄保留舊版。

`tools/prepare_github_release.py`用檔案及副檔名清單建立全新發布包，保留根與專案AGENTS、巢狀專案結構及逐檔雜湊。它不操作Git、不複製行情及日誌。`deployment/pages.yml`只手動發布已有site，沒有行情計算及每日排程；2026-10-11已上傳至根.github/workflows/pages.yml並部署成功。部署操作與結果版本approve分開，詳細範圍見DEPLOYMENT.md。

## v0.3本機工作管理

`workflow.run_workflow`組合既有application：前30樣本核對、診斷、完整前300、完全離線重播、待確認版本及報告。`jobs`提供OS作業鎖、來源失敗預算與觀測進度；`diagnostics`只讀已快取資料及雜湊驗證的官方原始回應，不抓資料、不推論停牌、不補價；`presentation`只呈現中文摘要與候選狀態。CLI與一鍵cmd只呼叫這些服務。

`recovery`提供官方原始回應重用及原交易日截止保護，舊recover_run入口也改用此共用模組。`CheckedDemandHistory`繼承原按需供應器，先檢查已完整覆蓋月份，再逐月載入並套用相同缺日／價格有效性排除。未覆蓋的月份是待取得資料，不當作確定缺日；正常screen預設供應器仍相容。核心KD與純選股條件不變。

工作識別含規則、快取、請求日期、分類快照及程式雜湊。進度不是唯一續跑依據：月份SQLite保留已完成下載，calculation及失敗預算保留在獨立工作目錄。重新啟動可重算既有股票但不重抓已完整月份；工作完成後相同入口顯示同一摘要，new-run才另建待確認版本。

OS鎖位於快取旁、程序結束自動釋放；不刪除鎖檔，避免並行解除／重建競態。進度寫入有限重試後記錄警告，必要原始回應／快取／結果存檔仍失敗停止。個股來源重試耗盡另記錄，不將失敗月份寫成完整行情；離線重播以同一份失敗紀錄重現排除，並嚴格禁止HTTP。

每份工作在完整離線核對成功後才新增結果版本；若SQLite提交後匯出中斷，續跑會依工作ID找回該版本，避免誤批准或反覆新增相同工作版本。樣本仍禁止進入每日版本庫。完整報告有資料不足時標示complete_with_exclusions及pending，不能視為正式發布或無缺資料驗收。

## 官方歷史價格備援

`price_fallback.OfficialPriceFallback`獨立於KD及選股核心，透過既有Source.daily與Cache讀取同日官方日報。DemandHistory及CheckedDemandHistory在缺價檢查前使用它；正常月份、排名行情、成交量與原始月份payload不覆寫。未知／失敗資料保留，不能產生推估K棒。CheckedDemandHistory仍先檢查缺交易日，避免替不可能通過的缺日月份多抓日報。

同一供應器共用日期請求與股票補缺記憶體；跨執行重用每日快取。報告`data_quality`保存原OHLC、補後OHLC、來源URL、日報列雜湊及未解原因；application將此內容納入輸入雜湊，將補缺政策納入規則雜湊，確保離線重播及跨版本比較能辨識資料政策差異。請求證據仍由原始回應存檔服務保存。新正式結果仍需明確確認，不將舊報告回寫為新政策。

## 呼叫方向

```text
CLI／未來 Actions、手機入口
  ├─ application.run_screen(request, config, cache, source, today)
  │    ├─ sources：官方資料取得與解析
  │    ├─ cache／demand：全市場日報、個股月份、日曆快取
  │    └─ screener → indicators／models：排名與純計算
  ├─ results.ResultStore：版本、正式指向、確認紀錄
  ├─ tracking.track_official：只讀正式結果，計算跨日差異
  ├─ verification：快取 KD 匯出、外部參考比對
  └─ output：JSON／CSV 呈現
```

## 模組契約

| 模組 | 責任 | 不應負責 |
| --- | --- | --- |
| config | YAML 合併、驗證及路徑解析 | 自動變更規則 |
| sources | 官方 I/O、schema 解析、原始回應證據 | 結果核准及選股判斷 |
| cache / demand | 行情覆蓋率、增量更新、歷史截止日 | 正式結果管理 |
| models / indicators / screener | 資料模型、KD、排名與選股 | CLI、網頁、GitHub、版本儲存 |
| application | ScreenRequest、工作流程、計算輸入與規則雜湊 | 列印、結果核准、部署 |
| results | ResultStore 契約與 SQLiteResultStore 實作 | 官方行情下載與指標計算 |
| tracking | 比較正式結果、指標差異及連續入選 | 重新選股、猜測缺失交易日 |
| verification | 已快取資料 KD 匯出與參考值比較 | 默認外部數值正確、宣告軟體驗收 |
| cli | 解析參數、建立依賴、呼叫服務、顯示結果與錯誤 | 複製選股公式 |

未來新增入口時應呼叫 application，不複製 CLI 或另寫一套指標；來源替換依 Source 契約，結果儲存替換依 ResultStore 契約。未使用來源與結果版本資料庫互相綁定的外鍵，兩者可獨立保存。

### 原始回應保存契約（2026-10-11修復）

`sources.OfficialSource.fetch`只對HTTP／網路失敗重試；取得回應後由`archive`保存證據。本機檔案失敗以OSError離開，讓application／CLI停止；不能轉成被screener逐股吞掉的DataError後繼續產生部分成功結果。行情內容／格式錯誤仍維持DataError的保守排除語意。

原始檔以`raw/v2/<SHA256前兩碼>/<SHA256>.gz`分層，優先重用已有`raw/<SHA256>.gz`，不搬移或刪除舊檔。`raw_response_path(raw_dir, digest)`驗證雜湊格式並定位新舊檔；新requests.jsonl紀錄帶相對raw_path，舊紀錄則使用定位函式。新增離線重播或封存工具不得假設所有雜湊檔都在raw根目錄。本次沒有加入完整原始回應重播服務。

## 結果版本生命週期

1. `screen` 成功計算：新增結果版本至 `paths.results`，仍匯出原有 JSON／CSV，輸出 version ID 與 pending 狀態。
2. `versions`、`show-result --version`：列出／檢閱版本。沒有正式版本時，按日期查詢明確失敗，不自動挑最新候選。
3. `approve --version ... --expected-current ...`：使用者確認後切換該日正式指向；首次使用 none，重算確認帶目前正式 ID。交易內檢查現行版本，與檢閱時不同則拒絕。
4. 舊版本保留。確認事件記錄新舊版本與時間；重複確認現行版不重複建立事件。
5. `track --date`：只讀正式結果，按確切前一交易日比較；重算只新增候選時不影響追蹤，正式切換後查詢重新計算。

SQLite 儲存完整 report JSON、內容 SHA-256、版本 ID、日期、時間；每次取版本檢查內容雜湊。這是本機正式結果來源，匯出檔是檢閱副本。副本的 pending 描述產生當時狀態；確認後以版本庫查詢為準，不回寫歷史副本。

application 記錄價格／分類／日曆及成功載入歷史的雜湊，以及規則與 Python 原始碼雜湊。雜湊供辨識差異，不能還原缺失原始資料。完整輸入快照封存與 GitHub 保存屬後續工作，不能宣稱現在可重播所有修訂版本。

## 追蹤語意

- 股票鍵為 `(market, code)`；新增=今日有、前日無；連續=兩日都有；退出=前日有、今日無。
- 日／週／月 K、D、量比、漲幅、成交股數差異只計算兩日都有的股票，缺值保留 null。
- 沒有前一交易日正式結果：insufficient_history，不跳到更早日期比較。
- 比較任一結果含錯誤：incomplete_data，不把資料故障誤判為退出。
- 規則雜湊不同或缺少：incompatible_rules，不把策略變動當一般入選變化。
- 連續天數不足歷史時標示 exact=false，表示下限；查到已知未入選日才 exact=true。中途錯誤／規則改變也停止確定天數。
- 每次回應附正式版本及比較基準版本；歷史正式版修訂後重新計算，避免保存過期差異。

## 相容、備份與下一步

### 少量驗證界線

`ScreenRequest.evaluation_limit`只限制排名完成後實際評估的候選數，不修改YAML的top_n。純核心保留完整前300排名及未評估數，樣本以report_scope=validation_sample區分。一般CLI沒有新增樣本預設，既有screen仍完整評估。

`tools/recover_run.py --limit 30`共用application及純核心，沿用既有恢復目錄的行情副本，另存validation-30工作紀錄；原失敗工作證據保留。工具補缺後自行離線重播並比對輸入與輸出，不建立每日結果版本。ResultStore在add及approve前均拒絕非full報告，避免樣本污染正式結果及追蹤。

進度JSON屬觀測輸出；有限重試後仍失敗則記錄警告，不影響選股計算。行情原始回應、行情資料庫及結果輸出屬必要資料，存檔失敗仍須停止。不能把進度檔暫時失敗的處理方式套用至必要資料。

原有指標與選股純核心保留；原 JSON／CSV 欄位保留，只增加版本與來源資訊。舊輸出不自動匯入或核准，避免把舊檔誤當正式版。

改動前備份位於 `.tmp/architecture-before-20261010-204833/`，不進 Git。此工作區新版與 D 槽原下載程序分開：未修改 D 槽程式或中斷其舊版本程序；舊程序輸出不會自動進入新版本庫。

Pages單日手機閱覽已於2026-10-11部署；GitHub行情持久資料格式、選股排程、手機觸發／確認及ChatGPT查詢尚未啟用。下階段持續處理看盤軟體比對、資料完整性與雲端快取驗證；不得將Actions臨時快取視為永久行情版本庫。
