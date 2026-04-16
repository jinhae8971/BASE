/* Crypto Research Agent dashboard — vanilla JS, no build step. */

const INDEX_URL = "reports/index.json";
const REPORT_URL = (date) => `reports/${date}.json`;

function $(sel) { return document.querySelector(sel); }
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
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

// -------------- Dashboard (index.html) --------------

async function renderDashboard() {
  let index;
  try {
    index = await loadJSON(INDEX_URL);
  } catch (e) {
    $("#hero-tagline").textContent = "No reports yet — waiting for the first daily run.";
    return;
  }
  if (!index || index.length === 0) {
    $("#hero-tagline").textContent = "No reports yet.";
    return;
  }

  const latest = index[0];
  $("#hero-tagline").textContent = `Latest: ${latest.date}`;

  // Fetch latest full report for narrative & top5 details
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
  const byId = Object.fromEntries((analyses || []).map(a => [a.coin_id, a]));
  gainers.forEach(g => {
    const a = byId[g.id];
    grid.appendChild(
      el("div", { class: "coin-card" },
        el("div", {},
          el("span", { class: "symbol" }, g.symbol.toUpperCase()),
          " ",
          el("span", { class: "pct" }, `+${g.change_48h_pct.toFixed(1)}%`),
        ),
        el("div", { class: "thesis" }, a ? a.pump_thesis : g.name),
      )
    );
  });
}

function renderHistory(index) {
  const ul = $("#history-list");
  ul.innerHTML = "";
  index.forEach(entry => {
    const topsyms = (entry.top5 || [])
      .map(t => `${t.symbol} +${t.change_48h_pct.toFixed(1)}%`)
      .join(" · ");
    const a = el("a", { href: `report.html?date=${entry.date}` },
      el("span", { class: "date" }, entry.date),
      entry.narrative_tagline || topsyms || "report"
    );
    ul.appendChild(el("li", {}, a));
  });
}

// -------------- Individual Report (report.html) --------------

async function renderReportPage() {
  const params = new URLSearchParams(window.location.search);
  let date = params.get("date");
  let index = [];
  try { index = await loadJSON(INDEX_URL); } catch (_) {}
  if (!date && index.length > 0) date = index[0].date;
  if (!date) {
    $("#report-title").textContent = "No report available";
    return;
  }

  $("#report-title").textContent = `Report — ${date}`;
  document.title = `Crypto Report · ${date}`;

  let report;
  try {
    report = await loadJSON(REPORT_URL(date));
  } catch (e) {
    $("#report-title").textContent = `Report ${date} not found`;
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
  const analysesById = Object.fromEntries((report.analyses || []).map(a => [a.coin_id, a]));
  (report.gainers || []).forEach(g => {
    const a = analysesById[g.id] || {};
    const card = el("div", { class: "analysis" });
    card.appendChild(el("h3", {},
      el("span", {}, `${g.symbol.toUpperCase()} · ${g.name}`),
      el("span", { class: "pct" }, `+${g.change_48h_pct.toFixed(1)}%`)
    ));
    card.appendChild(el("div", { class: "thesis muted" }, a.pump_thesis || ""));
    const tags = el("div", { class: "tags" });
    (a.category_tags || []).forEach(t => tags.appendChild(el("span", { class: "tag" }, t)));
    card.appendChild(tags);

    if (a.drivers && a.drivers.length) {
      card.appendChild(el("h4", {}, "Drivers"));
      const ul = el("ul");
      a.drivers.forEach(d => ul.appendChild(el("li", {}, d)));
      card.appendChild(ul);
    }
    if (a.risks && a.risks.length) {
      card.appendChild(el("h4", {}, "Risks"));
      const ul = el("ul");
      a.risks.forEach(d => ul.appendChild(el("li", {}, d)));
      card.appendChild(ul);
    }
    if (a.news_used && a.news_used.length) {
      card.appendChild(el("h4", {}, "News"));
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
