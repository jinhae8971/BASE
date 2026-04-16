/* KOSPI Research Agent dashboard — vanilla JS, no build step. */

const INDEX_URL = "reports/index.json";
const REPORT_URL = (date) => `reports/${date}.json`;

function $(sel) { return document.querySelector(sel); }
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "class") e.className = v;
    else e.setAttribute(k, v);
  });
  children.flat().forEach(c => {
    if (c == null) return;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  });
  return e;
}

async function loadJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

/* ─── Dashboard (index.html) ─── */

async function renderDashboard() {
  let index;
  try {
    index = await loadJSON(INDEX_URL);
  } catch (e) {
    $("#hero-tagline").textContent = "아직 보고서가 없습니다 — 첫 실행을 기다리는 중.";
    return;
  }
  if (!index || index.length === 0) {
    $("#hero-tagline").textContent = "아직 보고서가 없습니다.";
    return;
  }

  const latest = index[0];
  $("#hero-tagline").textContent = `최신: ${latest.date}`;

  try {
    const report = await loadJSON(REPORT_URL(latest.date));
    renderNarrative(report.narrative);
    renderTop5Grid(report.gainers, report.analyses);
  } catch (e) {
    console.warn("failed to load latest report", e);
  }

  renderHistory(index);
}

function renderNarrative(n) {
  if (!n) return;
  $("#narrative-card").hidden = false;
  $("#narrative-text").textContent = n.current_narrative || "";
  $("#hot-sectors").textContent = (n.hot_sectors || []).join(", ") || "—";
  $("#cooling-sectors").textContent = (n.cooling_sectors || []).join(", ") || "—";
  $("#insight-text").textContent = n.investment_insight || "";
}

function renderTop5Grid(gainers, analyses) {
  if (!gainers || gainers.length === 0) return;
  $("#top5-card").hidden = false;
  const grid = $("#top5-grid");
  grid.innerHTML = "";
  const byTicker = Object.fromEntries((analyses || []).map(a => [a.ticker, a]));
  gainers.forEach(g => {
    const a = byTicker[g.ticker];
    grid.appendChild(
      el("div", { class: "coin-card" },
        el("div", {},
          el("span", { class: "symbol" }, g.name),
          " ",
          el("span", { class: "pct" }, `+${g.change_pct_nd.toFixed(1)}%`),
        ),
        el("div", { class: "ticker-sub" }, g.ticker),
        el("div", { class: "thesis" }, a ? a.pump_thesis : ""),
      )
    );
  });
}

function renderHistory(index) {
  const ul = $("#history-list");
  ul.innerHTML = "";
  index.forEach(entry => {
    const topsyms = (entry.top5 || [])
      .map(t => `${t.name} +${t.change_pct_nd.toFixed(1)}%`)
      .join(" · ");
    const a = el("a", { href: `report.html?date=${entry.date}` },
      el("span", { class: "date" }, entry.date),
      entry.narrative_tagline || topsyms || "report"
    );
    ul.appendChild(el("li", {}, a));
  });
}

/* ─── Individual Report (report.html) ─── */

async function renderReportPage() {
  const params = new URLSearchParams(window.location.search);
  let date = params.get("date");
  let index = [];
  try { index = await loadJSON(INDEX_URL); } catch (_) {}
  if (!date && index.length > 0) date = index[0].date;
  if (!date) {
    $("#report-title").textContent = "보고서 없음";
    return;
  }

  $("#report-title").textContent = `보고서 — ${date}`;
  document.title = `KOSPI Report · ${date}`;

  let report;
  try {
    report = await loadJSON(REPORT_URL(date));
  } catch (e) {
    $("#report-title").textContent = `${date} 보고서를 찾을 수 없습니다`;
    return;
  }

  // Narrative
  const n = report.narrative || {};
  $("#narrative-text").textContent = n.current_narrative || "";
  $("#hot-sectors").textContent = (n.hot_sectors || []).join(", ") || "—";
  $("#cooling-sectors").textContent = (n.cooling_sectors || []).join(", ") || "—";
  $("#wow-text").textContent = n.week_over_week_change || "—";
  $("#insight-text").textContent = n.investment_insight || "";

  // Gainers + analyses
  const container = $("#gainers");
  container.innerHTML = "";
  const analysesByTicker = Object.fromEntries((report.analyses || []).map(a => [a.ticker, a]));
  (report.gainers || []).forEach(g => {
    const a = analysesByTicker[g.ticker] || {};
    const card = el("div", { class: "analysis" });
    card.appendChild(el("h3", {},
      el("span", {}, `${g.name} (${g.ticker})`),
      el("span", { class: "pct" }, `+${g.change_pct_nd.toFixed(1)}%`)
    ));
    card.appendChild(el("div", { class: "thesis muted" }, a.pump_thesis || ""));
    const tags = el("div", { class: "tags" });
    (a.sector_tags || []).forEach(t => tags.appendChild(el("span", { class: "tag" }, t)));
    card.appendChild(tags);

    if (a.drivers && a.drivers.length) {
      card.appendChild(el("h4", {}, "상승 요인"));
      const ul = el("ul");
      a.drivers.forEach(d => ul.appendChild(el("li", {}, d)));
      card.appendChild(ul);
    }
    if (a.risks && a.risks.length) {
      card.appendChild(el("h4", {}, "리스크"));
      const ul = el("ul");
      a.risks.forEach(d => ul.appendChild(el("li", {}, d)));
      card.appendChild(ul);
    }
    if (a.news_used && a.news_used.length) {
      card.appendChild(el("h4", {}, "관련 뉴스"));
      const ul = el("ul", { class: "news" });
      a.news_used.forEach(n => {
        ul.appendChild(el("li", {},
          el("a", { href: n.url, target: "_blank", rel: "noopener" }, n.title)
        ));
      });
      card.appendChild(ul);
    }
    container.appendChild(card);
  });

  // Nav buttons
  const dates = index.map(e => e.date);
  const idx = dates.indexOf(date);
  const prevBtn = $("#prev-btn");
  const nextBtn = $("#next-btn");
  const prev = idx >= 0 && idx < dates.length - 1 ? dates[idx + 1] : null;
  const next = idx > 0 ? dates[idx - 1] : null;
  if (prev) prevBtn.onclick = () => { window.location.search = `?date=${prev}`; };
  else prevBtn.disabled = true;
  if (next) nextBtn.onclick = () => { window.location.search = `?date=${next}`; };
  else nextBtn.disabled = true;
}

window.renderReportPage = renderReportPage;

// Auto-render dashboard page
if (document.getElementById("history-list") && !document.getElementById("gainers")) {
  renderDashboard();
}
