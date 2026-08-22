/* ============================================================
 * charts.js — minimal canvas charting, no external dependencies.
 *
 * The dashboard runs on a local machine that may well be offline while the
 * scheduler is trading, so pulling a charting library off a CDN is not an
 * option. These five primitives cover everything the UI needs:
 *   areaChart · barChart · donutChart · groupedBarChart · candleChart
 * ============================================================ */
(function (global) {
  'use strict';

  const COLORS = {
    text: '#97a3bb',
    faint: '#5f6b83',
    grid: '#1e2534',
    accent: '#4d8dff',
    up: '#2ecc8f',
    down: '#ff5c6c',
    purple: '#a97bff',
    amber: '#f5b74e',
  };
  const PALETTE = ['#4d8dff', '#2ecc8f', '#a97bff', '#f5b74e', '#ff5c6c', '#39c0c8', '#e77bd0', '#8f9bb3'];

  /** Size the backing store to the CSS box so lines stay crisp on retina. */
  function prepare(canvas) {
    const dpr = global.devicePixelRatio || 1;
    const cssWidth = canvas.clientWidth || canvas.parentElement.clientWidth || 600;
    const cssHeight = Number(canvas.getAttribute('height')) || 240;
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    canvas.style.height = cssHeight + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);
    ctx.font = '11px ui-monospace, Menlo, monospace';
    return { ctx, w: cssWidth, h: cssHeight };
  }

  function emptyState(ctx, w, h, message) {
    ctx.fillStyle = COLORS.faint;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(message || '데이터 없음', w / 2, h / 2);
  }

  function niceTicks(min, max, count) {
    if (!isFinite(min) || !isFinite(max)) return [0, 1];
    if (min === max) { min -= 1; max += 1; }
    const span = max - min;
    const raw = span / Math.max(count, 1);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
    const start = Math.floor(min / step) * step;
    const out = [];
    for (let v = start; v <= max + step * 0.5; v += step) out.push(v);
    return out;
  }

  function abbreviate(value) {
    const n = Number(value) || 0;
    const abs = Math.abs(n);
    if (abs >= 1e12) return (n / 1e12).toFixed(1) + '조';
    if (abs >= 1e8) return (n / 1e8).toFixed(1) + '억';
    if (abs >= 1e4) return (n / 1e4).toFixed(0) + '만';
    if (abs >= 100) return n.toFixed(0);
    if (abs >= 1) return n.toFixed(2);
    return n.toFixed(4);
  }

  /* ------------------------------------------------------------------
   * Area / line chart — the equity curve.
   * points: [{ label, value }]
   * ------------------------------------------------------------------ */
  function areaChart(canvas, points, options) {
    const opts = options || {};
    const { ctx, w, h } = prepare(canvas);
    if (!points || points.length === 0) return emptyState(ctx, w, h, opts.empty);

    const padL = 62, padR = 14, padT = 12, padB = 26;
    const plotW = w - padL - padR, plotH = h - padT - padB;
    const values = points.map((p) => Number(p.value) || 0);
    let min = Math.min.apply(null, values);
    let max = Math.max.apply(null, values);
    const pad = (max - min) * 0.12 || Math.abs(max || 1) * 0.05;
    min -= pad; max += pad;

    const ticks = niceTicks(min, max, 4);
    min = Math.min(min, ticks[0]);
    max = Math.max(max, ticks[ticks.length - 1]);

    const x = (i) => padL + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
    const y = (v) => padT + plotH - ((v - min) / (max - min || 1)) * plotH;

    ctx.strokeStyle = COLORS.grid;
    ctx.fillStyle = COLORS.faint;
    ctx.lineWidth = 1;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ticks.forEach((t) => {
      const ty = Math.round(y(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(padL, ty); ctx.lineTo(w - padR, ty); ctx.stroke();
      ctx.fillText(abbreviate(t), padL - 8, ty);
    });

    const rising = values[values.length - 1] >= values[0];
    const line = opts.color || (rising ? COLORS.up : COLORS.down);

    const gradient = ctx.createLinearGradient(0, padT, 0, padT + plotH);
    gradient.addColorStop(0, line + '44');
    gradient.addColorStop(1, line + '03');
    ctx.beginPath();
    ctx.moveTo(x(0), y(values[0]));
    values.forEach((v, i) => ctx.lineTo(x(i), y(v)));
    ctx.lineTo(x(values.length - 1), padT + plotH);
    ctx.lineTo(x(0), padT + plotH);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(x(0), y(values[0]));
    values.forEach((v, i) => ctx.lineTo(x(i), y(v)));
    ctx.strokeStyle = line;
    ctx.lineWidth = 1.8;
    ctx.lineJoin = 'round';
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(x(values.length - 1), y(values[values.length - 1]), 3, 0, Math.PI * 2);
    ctx.fillStyle = line;
    ctx.fill();

    ctx.fillStyle = COLORS.faint;
    ctx.textBaseline = 'top';
    const labelIdx = [0, Math.floor((points.length - 1) / 2), points.length - 1];
    [...new Set(labelIdx)].forEach((i, n, arr) => {
      ctx.textAlign = n === 0 ? 'left' : n === arr.length - 1 ? 'right' : 'center';
      ctx.fillText(points[i].label || '', x(i), padT + plotH + 8);
    });
  }

  /* ------------------------------------------------------------------
   * Vertical bar chart — daily realised P&L (signed colours).
   * ------------------------------------------------------------------ */
  function barChart(canvas, points, options) {
    const opts = options || {};
    const { ctx, w, h } = prepare(canvas);
    if (!points || points.length === 0) return emptyState(ctx, w, h, opts.empty);

    const padL = 62, padR = 14, padT = 12, padB = 26;
    const plotW = w - padL - padR, plotH = h - padT - padB;
    const values = points.map((p) => Number(p.value) || 0);
    let max = Math.max.apply(null, values.concat([0]));
    let min = Math.min.apply(null, values.concat([0]));
    const pad = (max - min) * 0.15 || 1;
    max += pad; min -= pad;

    const y = (v) => padT + plotH - ((v - min) / (max - min || 1)) * plotH;
    const ticks = niceTicks(min, max, 4);

    ctx.strokeStyle = COLORS.grid;
    ctx.fillStyle = COLORS.faint;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ticks.forEach((t) => {
      const ty = Math.round(y(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(padL, ty); ctx.lineTo(w - padR, ty); ctx.stroke();
      ctx.fillText(abbreviate(t), padL - 8, ty);
    });

    const slot = plotW / points.length;
    const barW = Math.max(Math.min(slot * 0.62, 26), 2);
    const zero = y(0);
    points.forEach((p, i) => {
      const v = Number(p.value) || 0;
      const cx = padL + slot * (i + 0.5);
      const top = Math.min(y(v), zero);
      const height = Math.max(Math.abs(y(v) - zero), 1);
      ctx.fillStyle = v >= 0 ? COLORS.up : COLORS.down;
      ctx.fillRect(cx - barW / 2, top, barW, height);
    });

    ctx.strokeStyle = COLORS.grid;
    ctx.beginPath();
    ctx.moveTo(padL, Math.round(zero) + 0.5);
    ctx.lineTo(w - padR, Math.round(zero) + 0.5);
    ctx.stroke();

    ctx.fillStyle = COLORS.faint;
    ctx.textBaseline = 'top';
    const step = Math.max(1, Math.ceil(points.length / 6));
    points.forEach((p, i) => {
      if (i % step) return;
      ctx.textAlign = 'center';
      ctx.fillText(p.label || '', padL + slot * (i + 0.5), padT + plotH + 8);
    });
  }

  /* ------------------------------------------------------------------
   * Horizontal grouped bars — the four score pillars per coin.
   * rows: [{ label, value, color? }]
   * ------------------------------------------------------------------ */
  function groupedBarChart(canvas, rows, options) {
    const opts = options || {};
    const { ctx, w, h } = prepare(canvas);
    if (!rows || rows.length === 0) return emptyState(ctx, w, h, opts.empty);

    const padL = 76, padR = 46, padT = 8, padB = 8;
    const plotW = w - padL - padR;
    const slot = (h - padT - padB) / rows.length;
    const barH = Math.min(slot * 0.55, 18);
    const max = opts.max || Math.max.apply(null, rows.map((r) => Number(r.value) || 0)) || 100;

    rows.forEach((row, i) => {
      const cy = padT + slot * (i + 0.5);
      const value = Number(row.value) || 0;
      const width = Math.max((value / max) * plotW, 1);

      ctx.fillStyle = COLORS.text;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(row.label, padL - 10, cy);

      ctx.fillStyle = COLORS.grid;
      ctx.fillRect(padL, cy - barH / 2, plotW, barH);
      ctx.fillStyle = row.color || PALETTE[i % PALETTE.length];
      ctx.fillRect(padL, cy - barH / 2, width, barH);

      ctx.fillStyle = COLORS.text;
      ctx.textAlign = 'left';
      ctx.fillText(opts.format ? opts.format(value) : value.toFixed(1), padL + plotW + 8, cy);
    });
  }

  /* ------------------------------------------------------------------
   * Donut — allocation breakdowns.
   * slices: [{ label, value, color? }]
   * ------------------------------------------------------------------ */
  function donutChart(canvas, slices, options) {
    const opts = options || {};
    const { ctx, w, h } = prepare(canvas);
    const data = (slices || []).filter((s) => (Number(s.value) || 0) > 0);
    if (data.length === 0) return emptyState(ctx, w, h, opts.empty);

    const total = data.reduce((sum, s) => sum + Number(s.value), 0);
    const cx = w / 2, cy = h / 2;
    const outer = Math.min(w, h) / 2 - 12;
    const inner = outer * 0.62;

    let angle = -Math.PI / 2;
    data.forEach((slice, i) => {
      const sweep = (Number(slice.value) / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(cx, cy, outer, angle, angle + sweep);
      ctx.arc(cx, cy, inner, angle + sweep, angle, true);
      ctx.closePath();
      ctx.fillStyle = slice.color || PALETTE[i % PALETTE.length];
      ctx.fill();
      angle += sweep;
    });

    ctx.fillStyle = '#e6ebf5';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = '600 15px ui-monospace, Menlo, monospace';
    ctx.fillText(opts.centerLabel || abbreviate(total), cx, cy - 6);
    ctx.font = '10px ui-monospace, Menlo, monospace';
    ctx.fillStyle = COLORS.faint;
    ctx.fillText(opts.centerSub || 'TOTAL', cx, cy + 11);
  }

  /* ------------------------------------------------------------------
   * Candlestick + volume — the coin detail chart.
   * candles: [{ time, open, high, low, close, volume }]
   * ------------------------------------------------------------------ */
  function candleChart(canvas, candles, options) {
    const opts = options || {};
    const { ctx, w, h } = prepare(canvas);
    if (!candles || candles.length === 0) return emptyState(ctx, w, h, opts.empty);

    const padL = 62, padR = 12, padT = 10, padB = 22;
    const volH = (h - padT - padB) * 0.22;
    const priceH = h - padT - padB - volH - 8;
    const plotW = w - padL - padR;

    const highs = candles.map((c) => Number(c.high));
    const lows = candles.map((c) => Number(c.low));
    let max = Math.max.apply(null, highs);
    let min = Math.min.apply(null, lows);
    const pad = (max - min) * 0.08 || max * 0.02;
    max += pad; min -= pad;

    const y = (v) => padT + priceH - ((v - min) / (max - min || 1)) * priceH;
    const slot = plotW / candles.length;
    const bodyW = Math.max(Math.min(slot * 0.62, 12), 1);

    ctx.strokeStyle = COLORS.grid;
    ctx.fillStyle = COLORS.faint;
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    niceTicks(min, max, 4).forEach((t) => {
      const ty = Math.round(y(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(padL, ty); ctx.lineTo(w - padR, ty); ctx.stroke();
      ctx.fillText(abbreviate(t), padL - 8, ty);
    });

    const maxVol = Math.max.apply(null, candles.map((c) => Number(c.volume) || 0)) || 1;
    const volTop = padT + priceH + 8;

    candles.forEach((c, i) => {
      const cx = padL + slot * (i + 0.5);
      const open = Number(c.open), close = Number(c.close);
      const bull = close >= open;
      const color = bull ? COLORS.up : COLORS.down;

      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(cx) + 0.5, y(Number(c.high)));
      ctx.lineTo(Math.round(cx) + 0.5, y(Number(c.low)));
      ctx.stroke();

      ctx.fillStyle = color;
      const top = y(Math.max(open, close));
      const height = Math.max(Math.abs(y(open) - y(close)), 1);
      ctx.fillRect(cx - bodyW / 2, top, bodyW, height);

      const vh = ((Number(c.volume) || 0) / maxVol) * volH;
      ctx.globalAlpha = 0.45;
      ctx.fillRect(cx - bodyW / 2, volTop + volH - vh, bodyW, vh);
      ctx.globalAlpha = 1;
    });

    ctx.fillStyle = COLORS.faint;
    ctx.textBaseline = 'top';
    const step = Math.max(1, Math.ceil(candles.length / 5));
    candles.forEach((c, i) => {
      if (i % step) return;
      ctx.textAlign = 'center';
      ctx.fillText(String(c.time || '').slice(5, 10), padL + slot * (i + 0.5), h - padB + 6);
    });
  }

  global.Charts = { areaChart, barChart, groupedBarChart, donutChart, candleChart, PALETTE, COLORS, abbreviate };
})(window);
