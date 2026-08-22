/* ============================================================
 * app.js — single-user Upbit trading dashboard.
 *
 * Seven tabs, one shared state object, no framework. Each tab has a `load`
 * function; switching tabs (or hitting refresh) calls it. A slow background
 * poll keeps the header KPIs and the scheduler card live.
 * ============================================================ */
(function () {
  'use strict';

  const state = {
    tab: 'overview',
    config: null,
    portfolio: null,
    equityDays: 30,
    analysisRunId: null,
    analysisItems: [],
    selectedMarket: null,
    trades: [],
  };

  /* ---------------- API ---------------- */
  async function api(path, options) {
    const opts = Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {});
    const token = localStorage.getItem('upbit_dashboard_token');
    if (token) opts.headers.Authorization = 'Bearer ' + token;
    const res = await fetch('/api' + path, opts);
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    // /api/health answers 503 when degraded; the body is the diagnosis, not an error.
    if (!res.ok && !(res.status === 503 && body && body.problems)) {
      throw new Error((body && (body.detail || body.message)) || ('HTTP ' + res.status));
    }
    return body;
  }
  const get = (p) => api(p);
  const post = (p, b) => api(p, { method: 'POST', body: JSON.stringify(b || {}) });
  const put = (p, b) => api(p, { method: 'PUT', body: JSON.stringify(b || {}) });
  const del = (p) => api(p, { method: 'DELETE' });

  /* ---------------- Formatting ---------------- */
  const krw = (v, digits) => {
    const n = Number(v) || 0;
    return n.toLocaleString('ko-KR', {
      minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0,
    });
  };
  const price = (v) => {
    const n = Number(v) || 0;
    if (n >= 1000) return n.toLocaleString('ko-KR', { maximumFractionDigits: 0 });
    if (n >= 1) return n.toLocaleString('ko-KR', { maximumFractionDigits: 2 });
    return n.toLocaleString('ko-KR', { maximumFractionDigits: 6 });
  };
  const pct = (v, digits) => ((Number(v) || 0) * 100).toFixed(digits === undefined ? 2 : digits) + '%';
  const signClass = (v) => (Number(v) > 0 ? 'up' : Number(v) < 0 ? 'down' : 'flat');
  const signed = (v) => (Number(v) > 0 ? '+' : '') + krw(v);
  const signedPct = (v, d) => (Number(v) > 0 ? '+' : '') + pct(v, d);
  const esc = (s) => String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  // Everything in this system is scheduled and reported in KST — the 09:10 scan,
  // the EOD flatten, the trading day boundary. Render in KST no matter where the
  // browser happens to be, so what you read matches what the strategy tab says.
  const KST = 'Asia/Seoul';

  function toDate(iso) {
    if (!iso) return null;
    // Store timestamps are naive UTC; scheduler timestamps carry an offset.
    const hasZone = /[+-]\d{2}:\d{2}$|Z$/.test(iso);
    const d = new Date(hasZone ? iso : iso + 'Z');
    return isNaN(d.getTime()) ? null : d;
  }

  function kstParts(d) {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: KST, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(d);
    const out = {};
    parts.forEach((p) => { out[p.type] = p.value; });
    if (out.hour === '24') out.hour = '00';
    return out;
  }

  function localTime(iso, withDate) {
    const d = toDate(iso);
    if (!d) return iso ? String(iso) : '—';
    const p = kstParts(d);
    return withDate === false ? `${p.hour}:${p.minute}` : `${p.month}-${p.day} ${p.hour}:${p.minute}`;
  }

  const dayKey = (iso) => {
    const d = toDate(iso);
    if (!d) return String(iso || '').slice(0, 10);
    const p = kstParts(d);
    return `${p.year}-${p.month}-${p.day}`;
  };

  /* ---------------- UI helpers ---------------- */
  const $ = (id) => document.getElementById(id);

  function toast(message, kind) {
    const host = $('toastHost');
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => el.remove(), kind === 'err' ? 7000 : 4000);
  }
  const fail = (err) => toast(err && err.message ? err.message : String(err), 'err');

  function confirmModal(title, body, requirePhrase) {
    return new Promise((resolve) => {
      const modal = $('modal'), input = $('modalInput');
      $('modalTitle').textContent = title;
      $('modalBody').textContent = body;
      input.value = '';
      input.classList.toggle('hidden', !requirePhrase);
      if (requirePhrase) input.placeholder = requirePhrase + ' 입력';
      modal.classList.remove('hidden');

      const done = (value) => {
        modal.classList.add('hidden');
        $('modalOk').onclick = null;
        $('modalCancel').onclick = null;
        resolve(value);
      };
      $('modalOk').onclick = () => {
        if (requirePhrase && input.value.trim() !== requirePhrase) {
          toast('확인 문구가 일치하지 않습니다.', 'err');
          return;
        }
        done(true);
      };
      $('modalCancel').onclick = () => done(false);
    });
  }

  function table(el, columns, rows, options) {
    const opts = options || {};
    if (!rows || rows.length === 0) {
      el.innerHTML = `<tbody><tr><td class="empty" colspan="${columns.length}">${esc(opts.empty || '데이터가 없습니다.')}</td></tr></tbody>`;
      return;
    }
    const head = columns.map((c) => `<th class="${c.align || ''}">${esc(c.title)}</th>`).join('');
    const body = rows.map((row, i) => {
      const cells = columns.map((c) => `<td class="${c.align || ''}">${c.render(row, i)}</td>`).join('');
      const attrs = opts.rowAttrs ? opts.rowAttrs(row, i) : '';
      return `<tr ${attrs}>${cells}</tr>`;
    }).join('');
    el.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
  }

  function kpi(label, value, sub, cls) {
    return `<div class="kpi">
      <div class="kpi-label">${esc(label)}</div>
      <div class="kpi-value ${cls || ''}">${value}</div>
      <div class="kpi-sub">${sub || ''}</div>
    </div>`;
  }

  function scoreCell(value) {
    const v = Number(value) || 0;
    return `<div class="scorebar">
      <span class="scorebar-val">${v.toFixed(1)}</span>
      <span class="scorebar-track"><span class="scorebar-fill" style="width:${Math.max(Math.min(v, 100), 0)}%"></span></span>
    </div>`;
  }

  function legend(el, items) {
    el.innerHTML = items.map((it) => `
      <span class="legend-item">
        <span class="legend-dot" style="background:${it.color}"></span>${esc(it.label)}
        <span class="legend-val">${it.value}</span>
      </span>`).join('');
  }

  /* ============================================================
   * Tab: 대시보드
   * ============================================================ */
  async function loadOverview() {
    const [pf, history, latest, control] = await Promise.all([
      get('/portfolio'),
      get('/portfolio/history?days=' + state.equityDays),
      get('/analyses/latest?limit=40'),
      get('/control/status'),
    ]);
    state.portfolio = pf;
    renderHeader(pf, control);

    const totalPnl = (pf.realized_pnl || 0) + (pf.unrealized_pnl || 0);
    const stats = pf.stats || {};
    $('kpiGrid').innerHTML = [
      kpi('총 자산', krw(pf.total_equity) + ' <small>KRW</small>',
        `트레이딩 ${krw(pf.tradable_equity)} · 장기보유 ${krw(pf.longterm_krw)}`),
      kpi('평가손익', signed(pf.unrealized_pnl), '보유 포지션 기준', signClass(pf.unrealized_pnl)),
      kpi('실현손익 (누적)', signed(pf.realized_pnl),
        `수수료 ${krw(stats.total_fee)} 차감 전`, signClass(pf.realized_pnl)),
      kpi('총 손익', signed(totalPnl), '실현 + 평가', signClass(totalPnl)),
      kpi('승률', ((stats.win_rate || 0) * 100).toFixed(1) + '%',
        `${stats.wins || 0}승 ${stats.losses || 0}패 / ${stats.closed_trades || 0}건`),
      kpi('현금 비중', pf.tradable_equity > 0 ? pct(pf.cash_krw / pf.tradable_equity, 1) : '—',
        `노출도 ${pct(pf.exposure_pct, 1)}`),
    ].join('');

    const points = (history.points || []).map((p) => ({ label: localTime(p.ts), value: p.total_krw }));
    Charts.areaChart($('equityChart'), points, { empty: '자산 스냅샷이 아직 없습니다.' });

    const slices = [
      { label: '현금 (KRW)', value: pf.cash_krw, color: Charts.PALETTE[0] },
      { label: '트레이딩 포지션', value: pf.trading_krw, color: Charts.PALETTE[1] },
      { label: '장기보유', value: pf.longterm_krw, color: Charts.PALETTE[2] },
    ];
    Charts.donutChart($('allocChart'), slices, { centerSub: '총 자산' });
    legend($('allocLegend'), slices.map((s) => ({
      color: s.color, label: s.label,
      value: krw(s.value) + (pf.total_equity > 0 ? ` (${pct(s.value / pf.total_equity, 1)})` : ''),
    })));

    renderPicks(latest);
    renderRegimePanel(latest && latest.run);
    renderEvents(control.events || []);
  }

  function renderPicks(latest) {
    const items = (latest && latest.items ? latest.items : []).filter((i) => i.selected);
    const fallback = (latest && latest.items ? latest.items : []).slice(0, 5);
    const rows = items.length ? items : fallback;
    $('pickRunInfo').textContent = latest && latest.run
      ? `${localTime(latest.run.started_at)} 실행 · ${latest.run.scanned || 0}종목 분석`
      : '아직 실행 이력이 없습니다.';

    table($('picksTable'), [
      { title: '종목', align: 'l', render: (r) => `<span class="symbol">${esc(r.symbol)}<small>${esc(r.korean_name || '')}</small></span>` },
      { title: '종합점수', render: (r) => scoreCell(r.total_score) },
      { title: '현재가', render: (r) => price(r.price) },
      { title: '24h', render: (r) => `<span class="${signClass(r.change_rate_24h)}">${signedPct(r.change_rate_24h, 1)}</span>` },
      { title: '거래대금', render: (r) => Charts.abbreviate(r.trade_price_24h) },
      { title: '진입', align: 'c', render: (r) => r.selected ? '<span class="pill buy">선정</span>' : '<span class="pill neutral">후보</span>' },
    ], rows, { empty: '선정된 종목이 없습니다. 분석내역 탭에서 스캔을 실행해 보세요.' });
  }

  function renderRegimePanel(run) {
    let regime = null;
    try { regime = run && run.payload_json ? JSON.parse(run.payload_json).regime : null; } catch (e) { regime = null; }
    const el = $('regimePanel');
    if (!regime) {
      el.innerHTML = '<p class="muted">아직 시장 국면 데이터가 없습니다.</p>';
      return;
    }
    const label = { risk_on: '위험선호 (알트 확대)', neutral: '중립 (노출 축소)', risk_off: '위험회피 (진입 중단)' };
    el.innerHTML = `
      <div class="regime-cell"><div class="k">국면</div><div class="v ${regime.regime === 'risk_off' ? 'down' : regime.regime === 'risk_on' ? 'up' : ''}">${esc(label[regime.regime] || regime.regime)}</div></div>
      <div class="regime-cell"><div class="k">노출 배수</div><div class="v">${(regime.exposure_multiplier || 0).toFixed(2)}x</div></div>
      <div class="regime-cell"><div class="k">BTC 가격</div><div class="v">${price(regime.btc_price)}</div></div>
      <div class="regime-cell"><div class="k">BTC 7일</div><div class="v ${signClass(regime.btc_return_7d)}">${signedPct(regime.btc_return_7d, 1)}</div></div>
      <div class="regime-cell"><div class="k">BTC RSI</div><div class="v">${(regime.btc_rsi || 0).toFixed(1)}</div></div>
      <div class="regime-cell"><div class="k">추세</div><div class="v">${regime.above_ma20 ? 'MA20↑' : 'MA20↓'} / ${regime.above_ma50 ? 'MA50↑' : 'MA50↓'}</div></div>
      <div class="regime-note">${esc(regime.rationale || '')}</div>`;
  }

  function renderEvents(events) {
    $('eventList').innerHTML = events.length
      ? events.slice(0, 40).map((e) => `
          <div class="event-row ${esc(e.level)}">
            <span class="event-time">${localTime(e.ts)}</span>
            <span class="event-msg">${esc(e.message)}</span>
          </div>`).join('')
      : '<p class="muted">기록된 이벤트가 없습니다.</p>';
  }

  let fallbackRegime = null;

  function renderHeader(pf, control) {
    if (control && control.recent_runs) {
      const run = control.recent_runs.find((r) => r.regime);
      if (run) fallbackRegime = run.regime;
    }
    $('topEquity').textContent = krw(pf.total_equity);
    const un = $('topUnrealized');
    un.textContent = signed(pf.unrealized_pnl);
    un.className = 'kpi-inline-value ' + signClass(pf.unrealized_pnl);

    const badge = $('modeBadge');
    badge.textContent = pf.mode === 'live' ? '● 실거래' : '○ 모의거래';
    badge.className = 'mode-badge ' + pf.mode;

    if (control && control.scheduler) renderScheduler(control.scheduler);
  }

  function renderScheduler(sched) {
    const jobs = sched.jobs || [];
    const selection = jobs.find((j) => j.id === 'selection');
    const health = sched.health || {};
    const degraded = health.healthy === false;
    const uptime = health.uptime_sec
      ? (health.uptime_sec >= 86400
          ? `${Math.floor(health.uptime_sec / 86400)}일 ${Math.floor((health.uptime_sec % 86400) / 3600)}시간`
          : `${Math.floor(health.uptime_sec / 3600)}시간 ${Math.floor((health.uptime_sec % 3600) / 60)}분`)
      : '—';

    $('schedBody').innerHTML = `
      <div><span class="${sched.running ? 'on' : 'off'}">${sched.running ? '● 실행 중' : '○ 중지됨'}</span>
        ${degraded ? '<span class="off" style="margin-left:6px">⚠ 이상</span>' : ''}</div>
      <div style="margin-top:4px">다음 선정: ${selection && selection.next_run ? localTime(selection.next_run) : '—'}</div>
      <div style="margin-top:2px">가동 ${uptime}</div>
      <div style="margin-top:2px;font-size:10.5px;opacity:.75">서버 ${localTime(sched.now)} KST</div>
      ${degraded ? `<div class="sched-problem">${(health.problems || []).map(esc).join('<br>')}</div>` : ''}`;

    const last = sched.last_results || {};
    const regimeValue = (last.selection && last.selection.regime && last.selection.regime.regime)
      || (last.monitor && last.monitor.regime)
      || fallbackRegime   // the scheduler has not fired in this process yet
      || null;
    const rb = $('regimeBadge');
    if (regimeValue) {
      const label = { risk_on: 'RISK-ON', neutral: 'NEUTRAL', risk_off: 'RISK-OFF' };
      rb.textContent = label[regimeValue] || regimeValue;
      rb.className = 'regime-badge ' + regimeValue;
    }
  }

  /* ============================================================
   * Tab: 포트폴리오
   * ============================================================ */
  async function loadPortfolio() {
    const pf = await get('/portfolio');
    state.portfolio = pf;

    const stats = pf.stats || {};
    $('pfKpi').innerHTML = [
      kpi('트레이딩 자산', krw(pf.tradable_equity), `현금 ${krw(pf.cash_krw)}`),
      kpi('포지션 평가액', krw(pf.trading_krw), `${(pf.open_positions || []).length}개 보유`),
      kpi('장기보유 평가액', krw(pf.longterm_krw), '자동매매 제외'),
      kpi('평가손익', signed(pf.unrealized_pnl), pct(pf.exposure_pct, 1) + ' 노출', signClass(pf.unrealized_pnl)),
      kpi('실현손익', signed(pf.realized_pnl),
        `PF ${stats.profit_factor == null ? '—' : stats.profit_factor.toFixed(2)}`, signClass(pf.realized_pnl)),
    ].join('');

    const managed = (pf.positions || []).filter((p) => p.engine_managed);
    const unmanaged = (pf.positions || []).filter((p) => !p.engine_managed);

    table($('positionsTable'), [
      { title: '종목', align: 'l', render: (r) => `<span class="symbol">${esc(r.symbol)}</span>` },
      { title: '수량', render: (r) => Number(r.balance).toFixed(6) },
      { title: '평단', render: (r) => price(r.avg_price) },
      { title: '현재가', render: (r) => price(r.price) },
      { title: '평가액', render: (r) => krw(r.value_krw) },
      { title: '손익', render: (r) => `<span class="${signClass(r.unrealized_pnl)}">${signed(r.unrealized_pnl)}</span>` },
      { title: '수익률', render: (r) => `<span class="${signClass(r.unrealized_pct)}">${signedPct(r.unrealized_pct)}</span>` },
      { title: '손절/익절', render: (r) => r.stop_price ? `${price(r.stop_price)} / ${price(r.take_price)}` : '—' },
      { title: '진입', render: (r) => r.opened_at ? localTime(r.opened_at) : '—' },
      {
        title: '', align: 'c',
        render: (r) => r.position_id
          ? `<button class="btn btn-sm btn-danger-text" data-close="${r.position_id}">청산</button>`
          : '<span class="muted">미관리</span>',
      },
    ], managed.concat(unmanaged), { empty: '보유 중인 트레이딩 포지션이 없습니다.' });

    $('positionsTable').querySelectorAll('[data-close]').forEach((btn) => {
      btn.onclick = async () => {
        const ok = await confirmModal('포지션 청산', '해당 포지션을 시장가로 즉시 청산합니다. 진행할까요?');
        if (!ok) return;
        try {
          const r = await post('/control/close', { position_id: Number(btn.dataset.close), reason: 'manual' });
          toast(`청산 완료 — ${signed(r.pnl)} KRW (${signedPct(r.pnl_pct)})`, 'ok');
          loadPortfolio();
        } catch (e) { fail(e); }
      };
    });

    table($('longtermTable'), [
      { title: '종목', align: 'l', render: (r) => `<span class="symbol">${esc(r.symbol)}</span>` },
      { title: '보호 수량', render: (r) => Number(r.quantity).toFixed(6) },
      { title: '매수평균', render: (r) => price(r.avg_buy_price) },
      { title: '현재가', render: (r) => price(r.price) },
      { title: '평가액', render: (r) => krw(r.value_krw) },
      { title: '수익률', render: (r) => `<span class="${signClass(r.unrealized_pct)}">${signedPct(r.unrealized_pct)}</span>` },
    ], pf.long_term || [], { empty: '등록된 장기보유 코인이 없습니다.' });

    const slices = (pf.positions || [])
      .filter((p) => p.value_krw > 0)
      .map((p, i) => ({ label: p.symbol, value: p.value_krw, color: Charts.PALETTE[i % Charts.PALETTE.length] }));
    slices.push({ label: '현금', value: pf.cash_krw, color: '#8f9bb3' });
    Charts.donutChart($('pfAllocChart'), slices, { centerSub: '트레이딩' });
    legend($('pfAllocLegend'), slices.map((s) => ({ color: s.color, label: s.label, value: krw(s.value) })));
  }

  /* ============================================================
   * Tab: 거래내역
   * ============================================================ */
  async function loadTrades() {
    const params = new URLSearchParams({ limit: '500' });
    const market = $('tradeMarketFilter').value.trim();
    const side = $('tradeSideFilter').value;
    const days = $('tradeDaysFilter').value;
    if (market) params.set('market', market.toUpperCase());
    if (side) params.set('side', side);
    if (days) params.set('days', days);

    const [data, stats] = await Promise.all([
      get('/trades?' + params.toString()),
      get('/trades/stats'),
    ]);
    state.trades = data.trades || [];
    const overall = stats.overall || {};

    $('tradeKpi').innerHTML = [
      kpi('청산 완료', (overall.closed_trades || 0) + '건',
        `${overall.wins || 0}승 ${overall.losses || 0}패`),
      kpi('승률', ((overall.win_rate || 0) * 100).toFixed(1) + '%',
        `평균 ${signedPct(overall.avg_pnl_pct)}`),
      kpi('실현손익', signed(overall.total_pnl), '수수료 반영', signClass(overall.total_pnl)),
      kpi('손익비', overall.profit_factor == null ? '—' : overall.profit_factor.toFixed(2),
        `익 ${signedPct(overall.avg_win_pct)} / 손 ${signedPct(overall.avg_loss_pct)}`),
      kpi('누적 수수료', krw(overall.total_fee), '매수 + 매도'),
    ].join('');

    table($('tradesTable'), [
      { title: '시각', align: 'l', render: (r) => localTime(r.ts) },
      { title: '종목', align: 'l', render: (r) => `<span class="symbol">${esc(r.symbol)}</span>` },
      { title: '구분', align: 'c', render: (r) => r.side === 'bid' ? '<span class="pill buy">매수</span>' : '<span class="pill sell">매도</span>' },
      { title: '체결가', render: (r) => price(r.price) },
      { title: '수량', render: (r) => Number(r.volume).toFixed(6) },
      { title: '금액', render: (r) => krw(r.krw_amount) },
      { title: '수수료', render: (r) => krw(r.fee, 0) },
      { title: '실현손익', render: (r) => r.pnl === null || r.pnl === undefined ? '—' : `<span class="${signClass(r.pnl)}">${signed(r.pnl)}</span>` },
      { title: '수익률', render: (r) => r.pnl_pct === null || r.pnl_pct === undefined ? '—' : `<span class="${signClass(r.pnl_pct)}">${signedPct(r.pnl_pct)}</span>` },
      { title: '점수', render: (r) => r.score ? Number(r.score).toFixed(1) : '—' },
      {
        title: '사유', align: 'l',
        render: (r) => `<span class="muted clip" title="${esc(r.reason || '')}">${esc(r.reason || '')}</span>`,
      },
      { title: '상태', align: 'c', render: (r) => `<span class="pill ${r.state === 'rejected' ? 'sell' : 'neutral'}">${esc(r.state)}</span>` },
    ], state.trades, { empty: '거래 내역이 없습니다.' });

    // Daily realised P&L
    const byDay = {};
    state.trades.filter((t) => t.pnl !== null && t.pnl !== undefined)
      .forEach((t) => { const k = dayKey(t.ts); byDay[k] = (byDay[k] || 0) + Number(t.pnl); });
    const pnlPoints = Object.keys(byDay).sort().map((k) => ({ label: k.slice(5), value: byDay[k] }));
    Charts.barChart($('pnlChart'), pnlPoints, { empty: '실현손익 기록이 없습니다.' });

    // Exit-reason mix
    const byReason = {};
    (stats.closed_positions || []).forEach((p) => {
      const k = p.exit_reason || 'unknown';
      byReason[k] = (byReason[k] || 0) + 1;
    });
    const labels = {
      take_profit: '익절', stop_loss: '손절', trailing_stop: '트레일링',
      time_exit: '시간 청산', regime_exit: '국면 청산', manual: '수동', panic: '전량 청산',
      daily_loss_kill: '일손실 한도', long_term_protected: '장기보유 전환',
      balance_missing: '잔고 없음', unknown: '기타',
    };
    const exitSlices = Object.keys(byReason).map((k, i) => ({
      label: labels[k] || k, value: byReason[k], color: Charts.PALETTE[i % Charts.PALETTE.length],
    }));
    Charts.donutChart($('exitChart'), exitSlices, { centerSub: '청산 건수' });
    legend($('exitLegend'), exitSlices.map((s) => ({ color: s.color, label: s.label, value: s.value + '건' })));
  }

  function exportTradesCsv() {
    if (!state.trades.length) return toast('내보낼 거래가 없습니다.', 'err');
    const cols = ['ts', 'mode', 'market', 'side', 'ord_type', 'price', 'volume', 'krw_amount', 'fee', 'pnl', 'pnl_pct', 'score', 'state', 'reason'];
    const csv = [cols.join(',')].concat(state.trades.map((t) =>
      cols.map((c) => `"${String(t[c] === null || t[c] === undefined ? '' : t[c]).replace(/"/g, '""')}"`).join(',')
    )).join('\n');
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `upbit_trades_${dayKey(new Date().toISOString())}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  /* ============================================================
   * Tab: 분석내역
   * ============================================================ */
  async function loadAnalysis() {
    const runs = (await get('/analyses/runs?limit=40')).runs || [];
    $('runStrip').innerHTML = runs.length
      ? runs.map((r) => `
          <div class="run-chip ${r.id === state.analysisRunId ? 'on' : ''}" data-run="${r.id}">
            <div class="rc-date">${localTime(r.started_at)}</div>
            <div class="rc-meta">${r.scanned || 0}종목 · 선정 ${r.selected || 0} · ${esc(r.regime || '-')}</div>
          </div>`).join('')
      : '<p class="muted">분석 실행 이력이 없습니다. 위의 “지금 스캔 실행”을 눌러보세요.</p>';

    $('runStrip').querySelectorAll('[data-run]').forEach((chip) => {
      chip.onclick = () => { state.analysisRunId = Number(chip.dataset.run); loadAnalysis(); };
    });

    const target = state.analysisRunId || (runs[0] && runs[0].id);
    if (!target) {
      table($('analysisTable'), [{ title: '종목', render: () => '' }], [], { empty: '분석 결과가 없습니다.' });
      return;
    }
    state.analysisRunId = target;

    const detail = await get('/analyses/' + target + '?limit=300');
    state.analysisItems = detail.items || [];
    $('analysisRunInfo').textContent = detail.run
      ? `${localTime(detail.run.started_at)} · ${detail.run.scanned || 0}종목 · ${esc(detail.run.note || '')}`
      : '';

    table($('analysisTable'), [
      { title: '#', align: 'l', render: (r) => r.rank || '' },
      { title: '종목', align: 'l', render: (r) => `<span class="symbol">${esc(r.symbol)}<small>${esc(r.korean_name || '')}</small></span>` },
      { title: '종합', render: (r) => scoreCell(r.total_score) },
      { title: '거래량', render: (r) => (r.volume_score || 0).toFixed(0) },
      { title: '수급', render: (r) => (r.flow_score || 0).toFixed(0) },
      { title: '차트', render: (r) => (r.tech_score || 0).toFixed(0) },
      { title: '베타', render: (r) => (r.beta_score || 0).toFixed(0) },
      { title: '24h', render: (r) => `<span class="${signClass(r.change_rate_24h)}">${signedPct(r.change_rate_24h, 1)}</span>` },
      { title: '거래대금', render: (r) => Charts.abbreviate(r.trade_price_24h) },
      { title: '선정', align: 'c', render: (r) => r.selected ? '<span class="pill buy">진입</span>' : '<span class="pill off">-</span>' },
    ], state.analysisItems, {
      empty: '분석 결과가 없습니다.',
      rowAttrs: (r) => `class="clickable ${r.market === state.selectedMarket ? 'selected' : ''}" data-market="${esc(r.market)}"`,
    });

    $('analysisTable').querySelectorAll('[data-market]').forEach((tr) => {
      tr.onclick = () => { state.selectedMarket = tr.dataset.market; renderScoreDetail(); };
    });
    if (state.analysisItems.length) {
      const known = state.analysisItems.some((i) => i.market === state.selectedMarket);
      if (!known) state.selectedMarket = state.analysisItems[0].market;
      renderScoreDetail();
    }
  }

  const METRIC_LABELS = {
    vol_surge_ratio: '거래대금 급증배수', vol_trend_5d_20d: '5일/20일 거래대금', vol_zscore: '거래대금 Z-score',
    turnover_24h_krw: '24h 거래대금', turnover_avg20_krw: '20일 평균 거래대금',
    orderbook_imbalance: '호가 불균형', orderbook_imbalance_top5: '상위5호가 불균형',
    taker_buy_ratio: '체결 매수비중', taker_buy_krw_ratio: '체결 매수비중(금액)',
    mfi_14: 'MFI(14)', obv_slope_10d: 'OBV 기울기',
    price: '현재가', ma5: 'MA5', ma20: 'MA20', ma60: 'MA60', rsi_14: 'RSI(14)',
    macd_hist: 'MACD 히스토그램', range_position_20d: '20일 레인지 위치', dist_to_high20: '20일 고점 이격',
    percent_b: '볼린저 %B', atr_pct: 'ATR 비율', trend_score: '추세 점수', momentum_score: '모멘텀 점수',
    breakout_score: '돌파 점수', volatility_score: '변동성 적합도', intraday_score: '인트라데이 점수',
    intraday_rsi: '인트라데이 RSI', intraday_above_ma20: '인트라데이 MA20 상회',
    beta_vs_btc_30d: 'BTC 대비 베타(30일)', return_7d: '7일 수익률', btc_return_7d: 'BTC 7일 수익률',
    relative_strength_7d: '상대강도(7일)', return_30d: '30일 수익률', realized_vol_20d: '실현변동성(20일)',
  };
  const PCT_METRICS = new Set(['return_7d', 'btc_return_7d', 'relative_strength_7d', 'return_30d',
    'atr_pct', 'dist_to_high20', 'realized_vol_20d']);
  const RATIO_METRICS = new Set(['taker_buy_ratio', 'taker_buy_krw_ratio', 'range_position_20d']);

  function formatMetric(key, value) {
    const n = Number(value);
    if (!isFinite(n)) return String(value);
    if (PCT_METRICS.has(key)) return signedPct(n, 2);
    if (RATIO_METRICS.has(key)) return pct(n, 1);
    if (key.endsWith('_krw')) return Charts.abbreviate(n);
    if (key === 'price' || key.startsWith('ma')) return price(n);
    if (Math.abs(n) >= 1000) return krw(n);
    return n.toFixed(Math.abs(n) < 1 ? 4 : 2);
  }

  function renderScoreDetail() {
    const item = state.analysisItems.find((i) => i.market === state.selectedMarket);
    const el = $('scoreDetail');
    if (!item) {
      el.innerHTML = '<p class="muted">종목을 선택하세요.</p>';
      return;
    }
    $('analysisTable').querySelectorAll('tbody tr').forEach((tr) => {
      tr.classList.toggle('selected', tr.dataset.market === state.selectedMarket);
    });

    const pillars = [
      { name: '거래량', value: item.volume_score, color: Charts.PALETTE[0] },
      { name: '수급', value: item.flow_score, color: Charts.PALETTE[1] },
      { name: '차트', value: item.tech_score, color: Charts.PALETTE[2] },
      { name: '베타', value: item.beta_score, color: Charts.PALETTE[3] },
    ];
    const metrics = item.metrics || {};
    const rows = Object.keys(metrics).map((k) =>
      `<div class="sd-metric"><span class="m-k">${esc(METRIC_LABELS[k] || k)}</span>
       <span class="m-v">${esc(formatMetric(k, metrics[k]))}</span></div>`).join('');

    el.innerHTML = `
      <div class="sd-head">
        <div>
          <div class="sd-title">${esc(item.symbol)} <span class="muted">${esc(item.korean_name || '')}</span></div>
          <div class="muted">${esc(item.market)} · ${price(item.price)} KRW ·
            <span class="${signClass(item.change_rate_24h)}">${signedPct(item.change_rate_24h, 1)}</span></div>
        </div>
        <div class="sd-total">${(item.total_score || 0).toFixed(1)}</div>
      </div>
      <div class="sd-pillars">
        ${pillars.map((p) => `<div class="sd-pillar"><div class="p-name">${p.name}</div>
          <div class="p-val" style="color:${p.color}">${(p.value || 0).toFixed(0)}</div></div>`).join('')}
      </div>
      <canvas id="pillarChart" height="130"></canvas>
      <p class="hint" style="margin-top:14px">${esc(item.reason || '')}</p>
      <div class="card-head"><h2>세부 지표</h2></div>
      <div class="sd-metrics">${rows || '<span class="muted">지표 없음</span>'}</div>
      <div class="card-head" style="margin-top:16px"><h2>일봉 차트</h2></div>
      <canvas id="detailCandles" height="200"></canvas>`;

    Charts.groupedBarChart($('pillarChart'),
      pillars.map((p) => ({ label: p.name, value: p.value, color: p.color })), { max: 100 });

    get('/market/candles?market=' + encodeURIComponent(item.market) + '&unit=day&count=90')
      .then((d) => Charts.candleChart($('detailCandles'), d.candles || []))
      .catch(() => Charts.candleChart($('detailCandles'), [], { empty: '차트를 불러오지 못했습니다.' }));
  }

  /* ============================================================
   * Tab: 전략
   * ============================================================ */
  const WEIGHT_META = [
    { key: 'volume', name: '거래량', desc: '거래대금 급증·유동성 — 돈이 오늘 들어오고 있는가' },
    { key: 'flow', name: '수급', desc: '호가 불균형·체결 강도·자금 흐름' },
    { key: 'technical', name: '차트', desc: '추세 정렬·모멘텀·돌파 위치·변동성 적합도' },
    { key: 'beta', name: '베타', desc: 'BTC 대비 베타와 상대강도 — 알트 펌핑 레버리지' },
  ];

  const FIELD_DEFS = {
    strategy: [
      ['max_positions', '최대 보유 종목 수', 'number', { min: 1, max: 20, step: 1 }],
      ['min_score', '최소 진입 점수', 'number', { min: 0, max: 100, step: 0.5 }],
      ['position_pct', '종목당 비중 (0~1)', 'number', { min: 0.01, max: 1, step: 0.01 }],
      ['max_krw_per_trade', '1회 최대 주문액 (0=무제한)', 'number', { min: 0, step: 10000 }],
      ['min_krw_per_trade', '1회 최소 주문액', 'number', { min: 5000, step: 1000 }],
      ['take_profit_pct', '익절 (0.05 = 5%)', 'number', { min: 0.001, max: 2, step: 0.005 }],
      ['stop_loss_pct', '손절 (0.03 = 3%)', 'number', { min: 0.001, max: 1, step: 0.005 }],
      ['trailing_activate_pct', '트레일링 발동 수익률', 'number', { min: 0, max: 2, step: 0.005 }],
      ['trailing_gap_pct', '트레일링 폭', 'number', { min: 0, max: 1, step: 0.005 }],
      ['max_hold_hours', '최대 보유 시간 (h)', 'number', { min: 0.5, max: 720, step: 0.5 }],
      ['order_style', '주문 방식', 'select', { options: ['market', 'limit'] }],
      ['limit_offset_bps', '지정가 오프셋 (bp)', 'number', { min: 0, max: 500, step: 1 }],
    ],
    risk: [
      ['daily_loss_kill_pct', '일일 손실 한도', 'number', { min: 0.005, max: 1, step: 0.005 }],
      ['max_total_exposure_pct', '최대 총 노출', 'number', { min: 0.05, max: 1, step: 0.05 }],
      ['min_cash_buffer_pct', '최소 현금 비중', 'number', { min: 0, max: 0.9, step: 0.05 }],
    ],
    schedule: [
      ['selection_time', '종목 선정 시각 (KST)', 'text', {}],
      ['monitor_interval_min', '모니터링 주기 (분)', 'number', { min: 1, max: 240, step: 1 }],
      ['eod_exit_time', '강제 청산 시각 (KST)', 'text', {}],
      ['snapshot_interval_min', '자산 스냅샷 주기 (분)', 'number', { min: 5, max: 1440, step: 5 }],
    ],
    universe: [
      ['min_trade_price_24h', '최소 24h 거래대금 (KRW)', 'number', { min: 0, step: 100000000 }],
      ['max_trade_price_24h', '최대 24h 거래대금 (0=무제한)', 'number', { min: 0, step: 100000000 }],
      ['min_price', '최소 가격 (KRW)', 'number', { min: 0, step: 1 }],
      ['max_candidates', '정밀 분석 종목 수', 'number', { min: 5, max: 200, step: 5 }],
      ['exclude_warning', '유의 종목 제외', 'bool', {}],
      ['exclude_caution', '주의 종목 제외', 'bool', {}],
      ['caution_types', '배제할 주의 유형 (쉼표 구분)', 'list', {}],
      ['exclude_stablecoins', '스테이블코인 제외', 'bool', {}],
      ['manual_blacklist', '수동 제외 (쉼표 구분)', 'list', {}],
    ],
  };

  function renderField(section, def, value) {
    const [key, label, kind, attrs] = def;
    const id = `cfg_${section}_${key}`;
    let input;
    if (kind === 'bool') {
      input = `<input type="checkbox" id="${id}" ${value ? 'checked' : ''} style="width:auto" />`;
    } else if (kind === 'select') {
      input = `<select id="${id}">${attrs.options.map((o) =>
        `<option value="${o}" ${o === value ? 'selected' : ''}>${o}</option>`).join('')}</select>`;
    } else if (kind === 'list') {
      input = `<input type="text" id="${id}" value="${esc((value || []).join(', '))}" />`;
    } else {
      const extra = Object.keys(attrs).map((k) => `${k}="${attrs[k]}"`).join(' ');
      input = `<input type="${kind}" id="${id}" value="${esc(value)}" ${extra} />`;
    }
    return `<label for="${id}">${esc(label)}</label>${input}`;
  }

  function readField(section, def) {
    const [key, , kind] = def;
    const el = $(`cfg_${section}_${key}`);
    if (!el) return undefined;
    if (kind === 'bool') return el.checked;
    if (kind === 'number') return Number(el.value);
    if (kind === 'list') return el.value.split(',').map((s) => s.trim()).filter(Boolean);
    return el.value;
  }

  async function loadStrategy() {
    const data = await get('/config');
    state.config = data.config;
    const cfg = data.config;

    const weights = cfg.scoring.weights;
    $('weightGrid').innerHTML = WEIGHT_META.map((meta) => `
      <div class="weight-item">
        <div class="weight-head">
          <span class="weight-name">${meta.name}</span>
          <span class="weight-val" id="wv_${meta.key}">${(weights[meta.key] * 100).toFixed(0)}%</span>
        </div>
        <div class="weight-desc">${meta.desc}</div>
        <input type="range" id="w_${meta.key}" min="0" max="100" step="1" value="${Math.round(weights[meta.key] * 100)}" />
      </div>`).join('');

    const drawWeights = () => {
      const raw = WEIGHT_META.map((m) => ({ key: m.key, name: m.name, value: Number($('w_' + m.key).value) }));
      const total = raw.reduce((s, r) => s + r.value, 0) || 1;
      raw.forEach((r) => { $('wv_' + r.key).textContent = ((r.value / total) * 100).toFixed(0) + '%'; });
      Charts.groupedBarChart($('weightChart'),
        raw.map((r, i) => ({ label: r.name, value: (r.value / total) * 100, color: Charts.PALETTE[i] })),
        { max: 100, format: (v) => v.toFixed(0) + '%' });
    };
    WEIGHT_META.forEach((m) => { $('w_' + m.key).oninput = drawWeights; });
    drawWeights();

    $('strategyForm').innerHTML = FIELD_DEFS.strategy.map((d) => renderField('strategy', d, cfg.strategy[d[0]])).join('');
    $('riskForm').innerHTML = FIELD_DEFS.risk.map((d) => renderField('risk', d, cfg.risk[d[0]])).join('');
    $('scheduleForm').innerHTML = FIELD_DEFS.schedule.map((d) => renderField('schedule', d, cfg.schedule[d[0]])).join('');
    $('universeForm').innerHTML = FIELD_DEFS.universe.map((d) => renderField('universe', d, cfg.universe[d[0]])).join('');
  }

  async function saveStrategy() {
    const raw = WEIGHT_META.map((m) => ({ key: m.key, value: Number($('w_' + m.key).value) }));
    const total = raw.reduce((s, r) => s + r.value, 0);
    if (total <= 0) return toast('가중치 합계가 0이면 저장할 수 없습니다.', 'err');

    const patch = { scoring: { weights: {} }, strategy: {}, risk: {}, schedule: {}, universe: {} };
    raw.forEach((r) => { patch.scoring.weights[r.key] = r.value / total; });
    ['strategy', 'risk', 'schedule', 'universe'].forEach((section) => {
      FIELD_DEFS[section].forEach((def) => {
        const value = readField(section, def);
        if (value !== undefined) patch[section][def[0]] = value;
      });
    });

    try {
      await put('/config', patch);
      toast('전략을 저장했습니다. 스케줄이 즉시 반영됩니다.', 'ok');
      loadStrategy();
    } catch (e) { fail(e); }
  }

  /* ============================================================
   * Tab: 장기보유
   * ============================================================ */
  async function loadLongTerm() {
    const [data, suggest] = await Promise.all([get('/holdings'), get('/holdings/suggest').catch(() => ({ candidates: [] }))]);

    table($('longtermManageTable'), [
      { title: '심볼', align: 'l', render: (r) => `<span class="symbol">${esc(r.symbol)}</span>` },
      { title: '보호 범위', align: 'l', render: (r) => r.protects_all ? '<span class="pill on">전량 보호</span>' : `<span class="pill neutral">${Number(r.locked_quantity).toFixed(6)} 개</span>` },
      { title: '메모', align: 'l', render: (r) => esc(r.memo || '') },
      { title: '', align: 'c', render: (r) => `<button class="btn btn-sm btn-danger-text" data-del="${esc(r.symbol)}">삭제</button>` },
    ], data.holdings || [], { empty: '등록된 장기보유 코인이 없습니다. 모든 코인이 거래 대상입니다.' });

    $('longtermManageTable').querySelectorAll('[data-del]').forEach((btn) => {
      btn.onclick = async () => {
        try {
          await del('/holdings/' + encodeURIComponent(btn.dataset.del));
          toast(btn.dataset.del + ' 을(를) 장기보유에서 해제했습니다.', 'ok');
          loadLongTerm();
        } catch (e) { fail(e); }
      };
    });

    const candidates = suggest.candidates || [];
    $('ltSuggest').innerHTML = candidates.length
      ? candidates.map((c) => `<button class="chip" data-sym="${esc(c.symbol)}" data-qty="${c.balance}">${esc(c.symbol)}<span class="q">${Number(c.balance).toFixed(4)}</span></button>`).join('')
      : '<span class="muted">등록 가능한 미등록 보유 자산이 없습니다.</span>';

    $('ltSuggest').querySelectorAll('[data-sym]').forEach((chip) => {
      chip.onclick = async () => {
        try {
          await post('/holdings', { symbol: chip.dataset.sym, locked_quantity: 0, memo: '계좌 보유분에서 등록' });
          toast(chip.dataset.sym + ' 을(를) 장기보유로 등록했습니다.', 'ok');
          loadLongTerm();
        } catch (e) { fail(e); }
      };
    });
  }

  /* ============================================================
   * Tab: 설정
   * ============================================================ */
  async function loadSettings() {
    const [creds, cfg] = await Promise.all([get('/credentials'), get('/config')]);
    state.config = cfg.config;

    const box = $('keyStatus');
    if (creds.configured) {
      box.className = 'status-box ok';
      box.textContent = `등록됨 (${creds.source === 'env' ? '환경변수' : '암호화 파일'}) — Access ${creds.access_key} / Secret ${creds.secret_key}`;
    } else {
      box.className = 'status-box' + (creds.error ? ' err' : '');
      box.textContent = creds.error || 'API 키가 등록되어 있지 않습니다. 모의 거래는 키 없이도 동작합니다.';
    }

    document.querySelectorAll('.mode-opt').forEach((btn) => {
      btn.classList.toggle('on', btn.dataset.mode === cfg.config.mode);
    });
    $('paperInitial').value = cfg.config.paper_initial_krw;

    await loadLogs();
  }

  async function loadLogs() {
    const level = $('logLevel').value;
    const data = await get('/logs?limit=300' + (level ? '&level=' + level : ''));
    table($('logTable'), [
      { title: '시각', align: 'l', render: (r) => localTime(r.ts) },
      { title: '레벨', align: 'c', render: (r) => `<span class="pill ${r.level === 'error' ? 'sell' : r.level === 'warning' ? 'neutral' : 'on'}">${esc(r.level)}</span>` },
      { title: '분류', align: 'l', render: (r) => esc(r.category) },
      { title: '내용', align: 'l', render: (r) => esc(r.message) },
    ], data.events || [], { empty: '로그가 없습니다.' });
  }

  /* ============================================================
   * Wiring
   * ============================================================ */
  const LOADERS = {
    overview: loadOverview,
    portfolio: loadPortfolio,
    trades: loadTrades,
    analysis: loadAnalysis,
    strategy: loadStrategy,
    longterm: loadLongTerm,
    settings: loadSettings,
  };
  const TITLES = {
    overview: '대시보드', portfolio: '포트폴리오', trades: '거래내역',
    analysis: '분석내역', strategy: '전략', longterm: '장기보유', settings: '설정',
  };

  async function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    document.querySelectorAll('.tab').forEach((s) => s.classList.toggle('active', s.id === 'tab-' + tab));
    $('pageTitle').textContent = TITLES[tab];
    try { await LOADERS[tab](); } catch (e) { fail(e); }
  }

  function bind() {
    document.querySelectorAll('.nav-item').forEach((b) => { b.onclick = () => switchTab(b.dataset.tab); });
    $('refreshBtn').onclick = () => switchTab(state.tab);

    $('equityRange').querySelectorAll('button').forEach((b) => {
      b.onclick = () => {
        $('equityRange').querySelectorAll('button').forEach((x) => x.classList.remove('on'));
        b.classList.add('on');
        state.equityDays = Number(b.dataset.days);
        loadOverview().catch(fail);
      };
    });

    $('snapshotBtn').onclick = async () => {
      try { await post('/portfolio/snapshot'); toast('자산 스냅샷을 기록했습니다.', 'ok'); loadPortfolio(); }
      catch (e) { fail(e); }
    };
    $('monitorBtn').onclick = async () => {
      try {
        const r = await post('/control/run/monitor');
        toast(`모니터링 완료 — ${r.checked || 0}개 확인, ${(r.closed || []).length}건 청산`, 'ok');
        loadPortfolio();
      } catch (e) { fail(e); }
    };

    $('tradeApply').onclick = () => loadTrades().catch(fail);
    $('tradeExport').onclick = exportTradesCsv;

    $('runScanBtn').onclick = async () => {
      const dry = $('dryRunCheck').checked;
      if (!dry) {
        const ok = await confirmModal('실주문 스캔',
          '시뮬레이션 체크를 해제한 상태입니다. 조건을 만족하면 실제로 매수 주문이 실행됩니다. 진행할까요?');
        if (!ok) return;
      }
      const btn = $('runScanBtn');
      btn.disabled = true; btn.textContent = '스캔 중…';
      try {
        const r = await post('/control/run/selection', { dry_run: dry });
        if (r.error) throw new Error(r.error);
        toast(`스캔 완료 — ${r.scanned || 0}종목 분석, ${(r.selected || []).length}종목 선정 (${r.elapsed_sec || 0}초)`, 'ok');
        state.analysisRunId = r.run_id;
        loadAnalysis();
      } catch (e) { fail(e); }
      finally { btn.disabled = false; btn.textContent = '지금 스캔 실행'; }
    };

    $('saveConfigBtn').onclick = saveStrategy;
    $('resetConfigBtn').onclick = async () => {
      const ok = await confirmModal('전략 초기화', 'config/settings.yaml 의 기본값으로 되돌립니다. 진행할까요?');
      if (!ok) return;
      try { await post('/config/reset'); toast('기본값으로 초기화했습니다.', 'ok'); loadStrategy(); }
      catch (e) { fail(e); }
    };

    $('ltAddBtn').onclick = async () => {
      const symbol = $('ltSymbol').value.trim();
      if (!symbol) return toast('심볼을 입력하세요.', 'err');
      try {
        await post('/holdings', {
          symbol,
          locked_quantity: Number($('ltQty').value) || 0,
          memo: $('ltMemo').value.trim(),
        });
        $('ltSymbol').value = ''; $('ltMemo').value = ''; $('ltQty').value = '0';
        toast(symbol.toUpperCase() + ' 을(를) 거래 대상에서 제외했습니다.', 'ok');
        loadLongTerm();
      } catch (e) { fail(e); }
    };

    $('saveKeyBtn').onclick = async () => {
      const access = $('accessKey').value.trim();
      const secret = $('secretKey').value.trim();
      if (!access || !secret) return toast('Access Key 와 Secret Key 를 모두 입력하세요.', 'err');
      try {
        await post('/credentials', { access_key: access, secret_key: secret });
        $('accessKey').value = ''; $('secretKey').value = '';
        toast('API 키를 암호화하여 저장했습니다.', 'ok');
        loadSettings();
      } catch (e) { fail(e); }
    };
    $('testKeyBtn').onclick = async () => {
      const box = $('keyStatus');
      box.className = 'status-box'; box.textContent = '연결 확인 중…';
      try {
        const r = await post('/credentials/test');
        box.className = 'status-box ' + (r.ok ? 'ok' : 'err');
        box.textContent = r.message + (r.ok ? ` · KRW 잔고 ${krw(r.krw_balance)}` : (r.hint ? ' · ' + r.hint : ''));
      } catch (e) { box.className = 'status-box err'; box.textContent = e.message; }
    };
    $('deleteKeyBtn').onclick = async () => {
      const ok = await confirmModal('API 키 삭제', '저장된 업비트 API 키를 삭제합니다. 실거래는 즉시 불가능해집니다.');
      if (!ok) return;
      try { await del('/credentials'); toast('API 키를 삭제했습니다.', 'ok'); loadSettings(); }
      catch (e) { fail(e); }
    };

    document.querySelectorAll('.mode-opt').forEach((btn) => {
      btn.onclick = async () => {
        const mode = btn.dataset.mode;
        if (mode === (state.config && state.config.mode)) return;
        if (mode === 'live') {
          const ok = await confirmModal('실거래 전환',
            '실제 자금으로 주문이 실행됩니다. 확인 문구를 입력하세요.', 'I-UNDERSTAND');
          if (!ok) return;
        }
        try {
          await post('/config/mode', { mode, confirm: mode === 'live' ? 'I-UNDERSTAND' : '' });
          toast(`거래 모드를 ${mode} 로 전환했습니다.`, mode === 'live' ? 'err' : 'ok');
          loadSettings();
        } catch (e) { fail(e); }
      };
    });

    $('resetPaperBtn').onclick = async () => {
      const ok = await confirmModal('모의 계좌 초기화', '모의 계좌의 잔고와 보유 코인을 모두 초기화합니다. 진행할까요?');
      if (!ok) return;
      try {
        await post('/control/paper/reset', { initial_krw: Number($('paperInitial').value) || 10000000 });
        toast('모의 계좌를 초기화했습니다.', 'ok');
        loadSettings();
      } catch (e) { fail(e); }
    };

    $('logLevel').onchange = () => loadLogs().catch(fail);

    $('panicBtn').onclick = async () => {
      const ok = await confirmModal('전량 청산 (킬 스위치)',
        '자동매매가 보유한 모든 포지션을 시장가로 즉시 청산합니다. 장기보유로 등록한 코인은 보호됩니다.',
        'I-UNDERSTAND');
      if (!ok) return;
      try {
        const r = await post('/control/panic', { confirm: 'I-UNDERSTAND' });
        toast(`전량 청산 — ${(r.closed || []).length}/${r.requested || 0}건 처리 (보호: ${(r.protected_symbols || []).join(', ') || '없음'})`, 'ok');
        switchTab(state.tab);
      } catch (e) { fail(e); }
    };
  }

  function renderHealthBadge(health) {
    const badge = $('healthBadge');
    if (!badge) return;
    if (!health || health.healthy !== false) {
      badge.classList.add('hidden');
      return;
    }
    badge.classList.remove('hidden');
    badge.textContent = `⚠ ${(health.problems || ['이상 감지'])[0]}`;
    badge.title = (health.problems || []).join('\n');
  }

  async function pollHeader() {
    try {
      const [pf, control, health] = await Promise.all([
        get('/portfolio'), get('/control/status'), get('/health'),
      ]);
      renderHeader(pf, control);
      renderHealthBadge(health && health.scheduler && health.scheduler.health);
    } catch (e) { /* transient — the next tick retries */ }
  }

  document.addEventListener('DOMContentLoaded', () => {
    bind();
    switchTab('overview');
    pollHeader();
    setInterval(pollHeader, 30000);
    window.addEventListener('resize', () => {
      clearTimeout(window.__resizeTimer);
      window.__resizeTimer = setTimeout(() => switchTab(state.tab), 250);
    });
  });
})();
