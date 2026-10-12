# 第一個手機報表發布計畫

## 2026-10-12新版更新

依SPEC第16節授權，官方OHLC備援新版完整重算與8項離線核對已完成，準備更新同一Pages網址。版本4295d7354ce54d2bbc41dcaee4ef0e76仍待確認；9入選／136條件不符／155資料不足。107筆必要缺價均在官方同日日報仍缺OHLC，補回0；不能宣稱資料已補齊。

新版網站匯出至`site-recompute-20261012-225856/`，舊`site/`保留。建立新發布包時指定`tools/prepare_github_release.py --site site-recompute-20261012-225856`；發布包中的`tw-stock-screener/site/`才是本次發布目標。完整重算與保留驗證見[RECOMPUTE_20261012.md](RECOMPUTE_20261012.md)。以下2026-10-11內容為首版發布紀錄，不能代替本次部署成功證據。

狀態：2026-10-11依使用者明確授權，程式及待確認試算報表已上傳，GitHub Pages部署成功。手機入口為[台股選股 KD 觀察站](https://papago2006.github.io/tw-stock-screener/)。最新範圍及完成證據見SPEC第14節；每日選股排程與結果approve不在本次範圍。

## 手機現在怎麼使用

用手機瀏覽器開啟上述網址即可，不必登入GitHub，也不需要讓本機電腦開機。可以加入書籤方便下次使用。輸入代號或名稱搜尋，選擇入選／全部候選／資料不足／條件不符，點開卡片查看日週月KD，亦可下載CSV。

目前是2026-10-08單日待確認試算快照，9檔入選；重新整理頁面只讀已發布資料，不啟動選股或更新日期。每日自動選股、手機手動執行、版本確認及跨日追蹤待後續整合。

## 實際發布證據

- 程式與網站提交：b8df3a84e51e7c2055e9c25fd3d1cc53a1399ea5；84個遠端檔案SHA-256與發布包github-pages-20261011-185431一致。
- [Pages發布工作](https://github.com/papago2006/tw-stock-screener/actions/runs/38133838579)成功，18:58台灣時間啟動，總耗時20秒。來源為GitHub Actions，只有手動workflow_dispatch，沒有選股排程。
- 正式網址與8個網站資產HTTP 200，資產逐位元組與發布包一致；390×844公開頁面搜尋、KD展開及9／300／155／136分類驗證通過，未觀測console錯誤。非手機實機驗收。
- 平台提示部分上游Actions原目標Node20、由GitHub以Node24執行，以及ubuntu-latest將切換映像；本次成功，留待後續檢查上游相容更新，不擅改選股規則。

以下保留發布範圍、重建方式與執行步驟，較早狀態敘述為發布前紀錄。

## 這次發布範圍

目標為 `papago2006/tw-stock-screener`。2026-10-11初次讀取為私人空儲存庫；使用者登入後已完成公開，最新回讀為public、size=0。帳號有管理及寫入權，本機工作區尚無commit及remote。寫入權不代表已授權上傳。

建議保留目前工作區的巢狀結構，讓既有開發規則繼續有效：

```text
repository/
  AGENTS.md                         # 原工作區開發規則，完整保留
  README.md
  .gitignore
  .github/workflows/pages.yml        # 手動發布靜態頁面，沒有排程
  tw-stock-screener/
    AGENTS.md                       # 專案層規則
    pyproject.toml
    config.yaml                     # 可攜式設定範例
    src/ tests/ tools/ docs/
    web/                            # 靜態介面原始檔
    site/                           # 只含公開報表及網站資產
```

獨立八法與舊Sites預覽不在這次包內，不移動、不刪除、不覆寫。

## 實際資料與顯示狀態

報表來自已保存版本 `2320938b3e3942e8b59324ad07c7f07f`，選股日2026-10-08。原完整工作於2026-10-11台灣時間16:10結束，300候選全部處理，入選9、條件不符136、資料不足155；另外36筆排名前資料問題。最終離線核對八項檢查一致，且重播無HTTP。這是資料有限制的試算，不代表完整市場資料無缺漏。

分類名單為2026-10-10，沿用current_universe警語；不視為同日分類的嚴格回測。KD外部軟體仍未選定，尚未驗收。報表只呈現原計算指標，沒有重新選股、放寬資料政策或補價。

使用者「我沒有問題了，接下來是部署上去嗎」是啟動部署準備的對話來源，不在此擅自解讀成對特定版本的approve或公開權限確認。結果資料庫沒有被批准；網站與公開JSON明示pending／待確認。公開試算頁與將結果確認成正式版是不同操作。

`web_export`以SQLite只讀模式取得版本、驗證內容雜湊，核對完整工作摘要的版本、工作ID、日期、數量及離線檢查，再用明確欄位清單匯出。公開資料包括代號、名稱、市場、排名、漲幅、精確成交股數、量比、日週月K/D、歷史起點、排除原因分類、規則及版本摘要；不匯出本機路徑、完整設定、請求日誌、原始例外或完整來源輸入。詳細問題證據繼續保留在本機。

手機頁可搜尋、切換入選／全部候選／資料不足／條件不符，展開KD，下載公開JSON及入選CSV；尚無歷史日期選單、跨日追蹤、選股啟動或確認按鈕，不顯示假的新增／退出訊號。

## 重建與本機檢閱

在Python專案目錄執行，僅讀版本及輸出網站，不下載行情：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m tw_stock_screener.web_export --results-db data/results.sqlite3 --version 2320938b3e3942e8b59324ad07c7f07f --summary output/jobs/f0dc501c525496756171/summary.json --output site
.\.venv\Scripts\python.exe -m http.server 8765 --bind 127.0.0.1 --directory site
```

本機網址為 `http://127.0.0.1:8765/`，只供這台電腦檢閱，手機不能用此網址遠端連入。也可直接開site/index.html，因data.js不依賴fetch即可載入。匯出到已有不同版本的目錄會拒絕，需換新目錄保留舊版。重新載入畫面不會重新計算；本次沒有Service Worker離線快取，避免誤以為舊資料是剛更新。

產生全新發布包與逐檔SHA-256清單：

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/prepare_github_release.py
```

產出於release/github-pages-時間戳，旁邊manifest.json列出精確檔案、位元組及SHA-256。工具不執行git操作，不遞迴複製整個工作區。排除行情SQLite、raw、local.yaml、虛擬環境、output、執行日誌、備份、八法、舊預覽、私人截圖與壓縮檔。既有行情及結果保留於原位置。

發布包包含正式規格及工程文件，文件本身會隨公開儲存庫可見。沒有附加授權條款；不得自行假設使用者已同意另行採用MIT或其他授權。

## 授權後的發布步驟

1. 使用者檢閱手機畫面、上傳清單及公開範圍，明確授權上傳與部署試算報表；儲存庫公開已另行授權並完成，不重問。
2. 再次讀取遠端狀態；若已有他人更新，停止覆蓋並先比較。從乾淨發布包上傳main，避免把整個工作區git add。
3. 目標儲存庫已公開；部署獲授權後，將Pages來源選為GitHub Actions。GitHub Free支援公開儲存庫Pages；未假設使用者目前方案。
4. 手動執行Publish mobile report。它只上傳tw-stock-screener/site及部署，沒有每日排程或行情API請求。
5. 以實際workflow成功、部署網址HTTP正常、手機版內容與結果一致作為成功證據。本次已於上述網址完成驗證；未來重發仍須核對對應版本與工作，不能沿用舊成功紀錄。

Actions模板依2026-10-11官方文件：checkout@v6、configure-pages@v5、upload-pages-artifact@v4、deploy-pages@v4；所需pages及OIDC權限限部署工作。模板目前僅在本機及發布包內，沒有在遠端執行。

- [GitHub Pages 官方介紹](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [自訂 Pages 工作流程](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)

## 後續獨立里程碑

網站上線可讓手機看已保存結果，尚不代表關機後可自動選股。後續先驗證大型快取的雲端備份／恢復與增量更新，不將數百MB原始資料直接加入一般Git歷史；儲存方式與保存政策仍待檢閱。再接選股Actions、已確認的18:30排程、手機手動執行及確認操作。排程完成仍先建立待確認結果，不能自動批准。

GitHub來源IP的官方API可用性須實測，不能用本機成功推論雲端一定成功。歷史版本與跨日追蹤按SPEC處理；KD看盤軟體比對及ChatGPT查詢仍保留後續。

## 本次本機驗證

- Python3.12完整111項測試通過，包含4項新增匯出邊界案例：私人路徑／例外不公開、樣本及錯誤摘要拒絕、唯讀正式狀態與內容雜湊、腳本逸出與保留舊版本。詳細日誌保留本機docs/test-results-pages.txt，不放入發布包。
- web/app.js經Node語法檢查通過；Pages模板經YAML結構檢查，尚未在GitHub實際執行。
- 真實版本的300候選與9檔入選各欄位逐一比對，公開資料值與保存值完全一致；公開JSON無本機路徑；原有21個Python模組與備份SHA-256一致。證據保留本機docs/pages-data-check.json。
- 本機瀏覽器390×844驗證：初始9檔、6504搜尋為1檔、日週月KD明細、資料不足155檔、全部候選300檔、搜尋無結果與空狀態、無水平溢出；未觀測console錯誤。截圖保留output/mobile-preview-390.png。
- 上述證明本機匯出與呈現；不代表已發布、雲端官方API已驗證、KD看盤軟體驗收或手機實機驗收。公開站完成後另驗證真實網址與部署工作。
