# 官方資料來源驗證（2026-10-10，台北）

開發前先完成來源直連、歷史資料格式與限制調查，並在對話回報架構後才建立核心程式。本機直連成功；搜尋服務存取 TPEx 網頁與 Swagger 曾回傳 403，因此未把搜尋服務失敗推論為所有環境不可用。

| 用途 | 官方端點與參數 | 已確認內容 |
|---|---|---|
| 上市每日全市場 | `https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=20250930&type=ALLBUT0999` | HTTP 200、stat=OK、date=20250930、tables 結構；1,308 筆，包括 ETF，必須另做普通股分類 |
| 上櫃每日全市場 | `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2025/09/30&id=&response=json` | HTTP 200、stat=ok、date=20250930、tables；合計 11,959 筆，含權證及管理股票表 |
| 上市個股月資料 | `https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=20250901&stockNo=2330` | 日期、成交股數、成交金額、開高低收、漲跌價差、成交筆數、註記；歷史月包含查詢日之後日期，使用時必須再截斷 |
| 上櫃個股月資料 | `https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?date=2025/09/01&code=6488&response=json` | 日期、**成交張數**、成交仟元、開高低收、漲跌、筆數；量比不使用此精度 |
| 上市／上櫃分類 | `https://isin.twse.com.tw/isin/C_public.jsp?strMode=2` / `strMode=4` | HTTP 200、CP950 HTML、最近更新日期=2026/10/10、CFI、掛牌日；篩出 1,054 上市普通股與 893 上櫃普通股 |
| 櫃買 OpenAPI 文件 | `https://www.tpex.org.tw/openapi/swagger.json` | 本機 HTTP 200；保留原始規格，但歷史日期行情採已實測的網站端點 |

首次完整回應壓縮保存在 `docs/probes/`，縮小的日行情 fixture 在 `tests/fixtures/`；日行情 fixture 的 `provenance.json` 保留完整回應 SHA-256。`tools/probe_initial.py` 可重新測試，需連網且不應在單元測試中自動呼叫。

每日欄位：

- TWSE：證券代號、證券名稱、成交股數、成交筆數、成交金額、開盤價、最高價、最低價、收盤價、漲跌(+/-)、漲跌價差、買賣揭示欄位、本益比。正負號可能含 HTML 標籤；價格可能為 `--`，特殊價差可能沒有可用基準。
- TPEx：代號、名稱、收盤、漲跌、開盤、最高、最低、均價、成交股數、成交金額(元)、成交筆數、買賣揭示欄位、發行股數、**次日**參考價與漲跌停價。次日欄位完全不參與當日排名或 KD。
- OHLC 為該日公布價格；未套還原／復權轉換。成交量統一採日報成交股數。兩來源使用各官方日報定義，未混入月資料張數。

2025-10-05 非交易日實測：TWSE `stat=很抱歉，沒有符合條件的資料!`，不帶 date；TPEx `stat=ok` 但明確列出 0 家、0 筆及空 data。因此 parser 只對已確認的無資料形式產生 NoData；任意錯誤、非 JSON、schema 變更都屬 DataError。快取另要求兩市場同日狀態一致。

另已直接測試 2010-01-04、2016-10-03、2025-09-30 兩市場行情，確認現行端點仍回傳歷史指定日資料。SQLite 實測下載 2025-09-29～30 後以離線方式重播成功。詳見 `live-checks.json`。這些是資料取得抽查，不代表已驗證所有歷史日期或完成全市場十年選股。

官方頁面：

- [TWSE 個股日成交資訊](https://www.twse.com.tw/zh/trading/historical/stock-day.html) 註明自民國 99 年 1 月 4 日提供；本專案統一以 2010-01-04 作為已驗證的保守歷史下限。
- [TPEx 上櫃股票行情](https://www.tpex.org.tw/zh-tw/mainboard/trading/info/pricing.html) 註明自民國 96 年起提供，較早資料有另行入口；本專案不混接未驗證舊端點。
- [TPEx 個股日成交資訊](https://www.tpex.org.tw/zh-tw/mainboard/trading/info/stock-pricing.html) 註明自民國 83 年 1 月起提供，與全市場日報服務範圍不同。
- [TWSE 交易制度介紹](https://wwwc.twse.com.tw/zh/products/system/trading.html) 說明前日收盤與除權息參考基準的差別；漲跌幅不可在特殊日隨意用未還原前日收盤替代。

未確認官方承諾的穩定下載配額與 SLA；預設 2 秒間隔只是保守工程設定，並非官方保證允許的頻率。API 可能受來源 IP、WAF、地區或服務狀態影響。正式長期使用應關注官方條款與改版；本專案失敗時停止、保留原因，沒有非官方替代行情。

歷史範圍、分類快照、跨市場銜接、首次回填成本與修訂資料限制完整列於 README。沒有以人工合成行情證明 API 可用或冒充選股結果。
## 2026-10-10 按需改版補充

`screen` 已改為排名後才查個股月份，日／週通過後才延長歷史，未使用日全市場回填。月查詢的每日未還原 OHLC 已投入 KD；TPEx 成交張數不轉成精確股數，歷史成交量保留 null，最終量比仍由日報取得。

新增官方日曆 `https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date=20250901`：實測 stat=OK、date=20250901，欄位為日期、成交股數、成交金額、成交筆數、發行量加權股價指數、漲跌點數。2025-09-29 不在官方交易日表中，不能把所有星期一至五當成交易日。真實 fixture 為 `tests/fixtures/twse_calendar.json`。

另外直接查詢兩市場 2026-10 個股月資料並截斷至 2026-10-08，台積電與環球晶均回傳 6 個交易日。更完整的日 KD／離線重播實測見 `demand-live-checks.json`；此檔不是全市場選股名單。
