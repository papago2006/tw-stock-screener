# 本機安裝紀錄

2026-10-10 已將專案安裝於 `D:\code\1010_k線`，使用本機 Python 3.13.5 建立 `.venv` 並完成 `pip install -e .`。

新增 Python 3.13 憑證格式相容處理：只針對 `Missing Subject Key Identifier` 重試較早版本的格式檢查，仍強制驗證 CA 憑證鏈與主機名稱；過期、錯誤主機名稱及其他憑證錯誤不放行。參考 [Python 官方 SSL 文件](https://docs.python.org/zh-tw/3.13/library/ssl.html)。

加入資料寫入前的磁碟空間檢查，保留至少 1 GiB；不足時停止並保留已下載快取，釋放空間後可續跑。選股參數維持原設定。

37 項測試全部通過，紀錄見 `test-results-python313.txt`。最新選股實際連線已成功解析共同最新交易日 2026-10-08，並開始從 2016-10 月回填官方行情。完成時間取決於網路、官方服務及磁碟容量。此紀錄不宣稱完整回填或選股已完成。

在此目錄查看進度：

```powershell
Get-Content .\output\run.log -Tail 20 -Wait
```

成功報告會出現在 `output\screen-*.json` 與 `output\screen-*.csv`。失敗會有 `output\failure-*.json`。請勿在下載執行中再開第二個選股程序；停止或失敗後，可用原命令續跑：

```powershell
.\.venv\Scripts\python.exe -m tw_stock_screener screen
```

## 按需改版狀態

舊版十年全市場回填程序已停止。新版依前 300 → 日 KD → 週 KD → 月 KD → 精確量比按需補資料，舊快取保留。測試已擴充為 55 項全部通過，見 `test-results-demand.txt`；此版本不再需要先完成全市場回填。日、週、月暖機範圍與初始化差異見最新版 README。
