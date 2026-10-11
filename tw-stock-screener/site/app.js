"use strict";
(() => {
  const data = window.SCREENER_REPORT;
  const $ = (id) => document.getElementById(id);
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const fmt = (value, digits = 2) => Number.isFinite(value) ? value.toLocaleString("zh-TW", {minimumFractionDigits: digits, maximumFractionDigits: digits}) : "—";
  const labels = {selected: "入選", condition_rejected: "條件不符", data_insufficient: "資料不足"};
  if (!data || data.schema_version !== 1 || !Array.isArray(data.candidates)) {
    $("notice").textContent = "報表載入失敗，請重新載入；不會顯示推測的選股結果。";
    return;
  }
  $("date").textContent = data.as_of;
  $("previous").textContent = `量比基準日 ${data.previous_session}`;
  const official = data.publication_status === "official";
  $("publication").textContent = official ? "已確認版本" : "待確認 · 試算";
  $("publication").className = `pill ${official ? "official" : "pending"}`;
  $("notice-title").textContent = official ? "已確認版本，仍有資料限制" : "本次已完成計算，尚未成為正式結果";
  $("notice").textContent = `已處理 ${data.outcomes.processed} 檔候選；${data.outcomes.data_insufficient} 檔因資料不足排除，另有 ${data.outcomes.ranking_errors} 筆排名前資料問題。缺資料不視為條件不符。KD 看盤軟體比對尚未驗收。`;
  for (const [label, value, note] of [
    ["入選股票", data.outcomes.selected, "日、週、月 KD 與量比通過"],
    ["已處理候選", data.outcomes.processed, `可排名 ${data.counts.rankable} 檔中取前 ${data.rules.selection.top_n}`],
    ["條件不符", data.outcomes.condition_rejected, "在其中一個篩選階段未通過"],
    ["資料不足", data.outcomes.data_insufficient, "保守排除，保留原因"],
  ]) {
    const node = el("div", undefined, "metric");
    node.append(el("span", label), el("strong", String(value)), el("small", note));
    $("metrics").append(node);
  }
  function card(row) {
    const node = el("article", undefined, "stock");
    const title = el("div", undefined, "stock-title");
    const identity = el("div");
    const code = el("div", row.code, "stock-code");
    code.append(el("span", row.market === "TWSE" ? "上市" : "上櫃", "market"));
    identity.append(code, el("h3", row.name));
    title.append(identity, el("span", labels[row.outcome], `pill ${row.outcome}`));
    const numbers = el("div", undefined, "numbers");
    const gain = el("div");
    gain.append(el("small", `漲幅排名 #${row.rank}`), el("strong", `${row.change_pct > 0 ? "+" : ""}${fmt(row.change_pct)}%`, row.change_pct >= 0 ? "gain" : "loss"));
    const ratio = el("div");
    ratio.append(el("small", "當日 / 前日量比"), el("strong", row.volume_ratio === null ? "—" : `${fmt(row.volume_ratio)} 倍`));
    numbers.append(gain, ratio);
    const details = el("details");
    details.append(el("summary", "查看日・週・月 KD 與成交量"));
    const table = el("table");
    const head = el("thead"), hr = el("tr");
    ["尺度", "K", "D", "K > D"].forEach((text) => hr.append(el("th", text)));
    head.append(hr); table.append(head);
    const body = el("tbody");
    for (const [prefix, label] of [["daily", "日"], ["weekly", "週"], ["monthly", "月"]]) {
      const tr = el("tr"), k = row[`${prefix}_k`], d = row[`${prefix}_d`];
      const signal = Number.isFinite(k) && Number.isFinite(d) ? (k > d ? "通過" : "未通過") : "未計算";
      [label, fmt(k, 4), fmt(d, 4), signal].forEach((text) => tr.append(el("td", text)));
      body.append(tr);
    }
    table.append(body); details.append(table);
    details.append(el("p", `當日成交量 ${fmt(row.volume, 0)} 股（${fmt(Number.isFinite(row.volume) ? row.volume / 1000 : null, 3)} 張）。量比使用官方日報精確股數。`, "volume"));
    details.append(el("p", "先前階段未通過或資料不足時，後續指標保留未計算。表格四捨五入僅供顯示，通過判斷沿用原始結果。", "muted"));
    node.append(title, numbers, el("p", row.reason, "reason"), details);
    return node;
  }
  function render() {
    const query = $("search").value.trim().toLocaleLowerCase("zh-TW");
    const filter = $("filter").value;
    const rows = data.candidates.filter((row) => (filter === "all" || row.outcome === filter) && `${row.code} ${row.name}`.toLocaleLowerCase("zh-TW").includes(query));
    $("stocks").replaceChildren(...rows.map(card));
    $("list-count").textContent = `顯示 ${rows.length} 檔 · 依原漲幅排名排序`;
    $("empty").hidden = rows.length !== 0;
    $("results-title").textContent = filter === "selected" ? "入選觀察清單" : filter === "all" ? "全部候選處理結果" : labels[filter];
  }
  $("search").addEventListener("input", render);
  $("filter").addEventListener("change", render);
  $("reload").addEventListener("click", () => window.location.reload());
  for (const [label, value] of [["合併市場漲幅排名", data.counts.top_n], ["日 KD：K > D", data.counts.daily_kd], ["週 KD：K > D", data.counts.weekly_kd], ["月 KD：K > D", data.counts.monthly_kd], [`量比 ≥ ${data.rules.selection.volume_ratio}`, data.counts.volume_ratio]]) {
    $("stages").append(el("li", `${label} → ${value} 檔`));
  }
  const kd = data.rules.kd;
  $("rules").textContent = `KD (${kd.period}, ${kd.k_smoothing}, ${kd.d_smoothing})；初始 K、D ${kd.initial}；各尺度至少 ${kd.period + kd.warmup_periods} 根棒。日／週／月歷史窗口 ${data.rules.history.daily_months}／${data.rules.history.weekly_months}／${data.rules.history.months} 個月。`;
  const current = data.universe.mode === "current_universe";
  for (const text of [
    "同一快取離線重算一致，重算過程無 HTTP 請求。這不代表外部軟體 KD 已驗收或全市場資料無缺漏。",
    `普通股名單日期 ${data.universe.as_of || "未知"}。${current ? "使用當前分類，並非同日分類名單的嚴格歷史回測。" : "分類口徑沿用結果版本。"}`,
    `普通股名單 ${data.counts.universe} 檔；有行情 ${data.counts.quoted} 檔；可排名 ${data.counts.rankable} 檔。`,
  ]) $("coverage").append(el("p", text));
  for (const error of data.ranking_errors) $("ranking-errors").append(el("li", `${error.market} ${error.code}：${error.reason}`));
  $("tracking").textContent = data.tracking.reason;
  $("version").textContent = `版本 ${data.version} · 結果 SHA-256 ${data.report_sha256}`;
  $("generated").textContent = `計算時間 ${new Date(data.generated_at).toLocaleString("zh-TW", {timeZone: "Asia/Taipei", hour12: false})}（台灣時間）`;
  render();
})();
