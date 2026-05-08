"""Streamlit operational dashboard — primary operator surface.

Run inside the Docker stack via the ``dashboard`` service:

    docker compose up dashboard

Standalone:

    streamlit run src/dashboard/app.py

Pages (sidebar):
    📈 Overview     — NAV curve + flag badges + 30d decision mix
    ☀️ Today        — today's target, regime, imminent stops, send report
    💼 Positions    — per-name PnL / peak / trailing% / pyramid level
    🔬 X-ray        — factor + sector exposure of the live book
    🤖 Learning     — TWAP bandit / consensus booster / A/B paper books
    🚨 Alerts       — last 7d guard / stop / replace / param-update events
    🧾 Journal      — searchable journal w/ outcome attribution
    ⏪ Backtest     — quant-only historical simulation
    🎛️ Control      — feature-flag toggles, risk-param sliders, blacklist,
                       kill switch (typed-confirm gated)
    🪞 Reflection   — read weekly report, approve auto-apply patches

Sidebar Quick actions:
    ⏯ Run research now     — synchronous research_phase() (UI blocks ~60-120s
                              while the four LLM specialists run)
    📅 EOD reconcile now   — synchronous eod_phase() (~5-10s)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import streamlit as st  # noqa: E402

from common.config import get_env, get_setting  # noqa: E402
from memory.journal import DecisionJournal  # noqa: E402

st.set_page_config(
    page_title="MAI-System",
    page_icon="📈",
    layout="wide",
)

# Sidebar — global controls reused across pages
with st.sidebar:
    auto_refresh = st.checkbox("Auto-refresh (60s)", value=False)
    if auto_refresh:
        # Lightweight refresh — meta tag triggers a full reload every 60s.
        st.markdown(
            '<meta http-equiv="refresh" content="60">', unsafe_allow_html=True
        )

    st.divider()
    st.subheader("Quick actions")
    st.caption(
        "These run synchronously and **block this tab** until done. "
        "Open another tab to keep monitoring."
    )

    if st.button("⏯ Run research now", use_container_width=True):
        try:
            from scheduler.daily_pipeline import research_phase

            with st.spinner("Running 4 specialist agents (~60-120s)…"):
                research_phase()
            st.success("Research finished — see Today page.")
        except Exception as e:
            st.error(f"research failed: {e}")

    if st.button("📅 EOD reconcile now", use_container_width=True):
        try:
            from scheduler.daily_pipeline import eod_phase

            with st.spinner("Running EOD (~5-10s)…"):
                eod_phase()
            st.success("EOD reconciliation done.")
        except Exception as e:
            st.error(f"eod failed: {e}")


def _pct(v: float) -> str:
    """Color-coded HTML span for a +/- percentage."""
    color = "#22c55e" if v >= 0 else "#dc2626"
    return f'<span style="color:{color};font-weight:600">{v:+.2%}</span>'


@st.cache_data(ttl=60)
def _journal_recent(days: int) -> pd.DataFrame:
    j = DecisionJournal()
    rows = j.recent(lookback_days=days)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _page_overview() -> None:
    st.title("📈 MAI-System — 운영 대시보드")
    env = get_env()

    # Live NAV / Δ% / MDD up top
    try:
        from portfolio.risk_guards import load_recent_equity

        eq = load_recent_equity(lookback_days=60)
        nav_now = float(eq.iloc[-1]) if not eq.empty else 0.0
        nav_chg = (
            float(eq.iloc[-1]) / float(eq.iloc[-2]) - 1
            if len(eq) >= 2
            else 0.0
        )
        rolling_mdd = (
            float(((eq - eq.cummax()) / eq.cummax()).min())
            if len(eq) >= 2
            else 0.0
        )
    except Exception:
        eq, nav_now, nav_chg, rolling_mdd = pd.Series(dtype=float), 0.0, 0.0, 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NAV (KRW)", f"{nav_now:,.0f}", f"{nav_chg:+.2%}")
    c2.metric("Rolling MDD", f"{rolling_mdd:.2%}")
    c3.metric("KIS 환경", env.kis_env.upper())
    c4.metric("MDD 한도", f"{get_setting('risk.max_portfolio_mdd', 0.15):.0%}")

    if not eq.empty:
        st.subheader("NAV curve")
        st.line_chart(eq.rename("NAV"))

    # Active feature flags — visual badges
    st.subheader("Feature flags")
    flags = {
        "Hedge": get_setting("hedge.enabled", False),
        "Websocket": get_setting("websocket.enabled", False),
        "TWAP": get_setting("execution.twap_enabled", False),
        "TWAP bandit": get_setting("execution.twap_bandit_enabled", False),
        "AB rotate": get_setting("optimizer.ab_auto_rotate", False),
        "Booster auto": get_setting("learning.auto_apply", False),
    }
    badges = "  ".join(
        f"<span style='background:{'#22c55e' if v else '#475569'};"
        f"color:white;padding:3px 9px;border-radius:12px;font-size:0.85rem'>"
        f"{name} {'ON' if v else 'off'}</span>"
        for name, v in flags.items()
    )
    st.markdown(badges, unsafe_allow_html=True)

    st.divider()
    st.subheader("최근 30일 의사결정")
    df = _journal_recent(30)
    if df.empty:
        st.info("아직 저널 기록이 없습니다. `python scripts/run_daily.py` 로 시작하세요.")
        return

    by_agent = (
        df.groupby("agent")
        .agg(decisions=("id", "count"), avg_conviction=("conviction", "mean"))
        .reset_index()
        .sort_values("decisions", ascending=False)
    )
    st.dataframe(by_agent, use_container_width=True, hide_index=True)

    st.subheader("일자별 의사결정 추이")
    daily = df.groupby([df["ts"].dt.date, "agent"]).size().unstack(fill_value=0)
    st.line_chart(daily)


def _page_positions() -> None:
    st.title("💼 현재 포지션")
    try:
        from broker.kis_client import KISClient
        from portfolio.position_state import all_states

        state = KISClient().get_account_state()
        states = {s.ticker: s for s in all_states()}
    except Exception as e:
        st.error(f"KIS 잔고 조회 실패: {e}")
        return
    if not state["positions"]:
        st.info("보유 포지션이 없습니다.")
        return

    rows = []
    for t, q in state["positions"].items():
        px = state["prices"].get(t, 0.0)
        s = states.get(t)
        pnl = (px / s.entry_price - 1.0) if (s and s.entry_price) else 0.0
        trail = (px / s.peak_price - 1.0) if (s and s.peak_price) else 0.0
        rows.append(
            {
                "ticker": t,
                "qty": q,
                "entry": s.entry_price if s else 0,
                "peak": s.peak_price if s else 0,
                "now": px,
                "value": q * px,
                "pnl%": pnl,
                "trail%": trail,
                "pyramid": s.pyramid_levels if s else 0,
            }
        )
    df = pd.DataFrame(rows).sort_values("value", ascending=False)
    st.dataframe(
        df.style.format(
            {
                "entry": "{:,.0f}",
                "peak": "{:,.0f}",
                "now": "{:,.0f}",
                "value": "{:,.0f}",
                "pnl%": "{:+.2%}",
                "trail%": "{:+.2%}",
            }
        ).map(
            lambda v: (
                "color: #22c55e"
                if isinstance(v, (int, float)) and v > 0
                else "color: #dc2626"
                if isinstance(v, (int, float)) and v < 0
                else None
            ),
            subset=["pnl%", "trail%"],
        ),
        use_container_width=True,
        hide_index=True,
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("현금 (KRW)", f"{state['cash']:,.0f}")
    col2.metric("순자산 (KRW)", f"{state['nav']:,.0f}")
    col3.metric("평균 PnL", f"{df['pnl%'].mean():+.2%}" if not df.empty else "0.00%")


def _page_journal() -> None:
    st.title("🧾 의사결정 저널")
    days = st.slider("Lookback (days)", min_value=1, max_value=180, value=30)
    df = _journal_recent(days)
    if df.empty:
        st.info("기록 없음")
        return

    agent = st.selectbox(
        "Agent", ["(전체)", *sorted(df["agent"].unique().tolist())]
    )
    if agent != "(전체)":
        df = df[df["agent"] == agent]
    st.dataframe(
        df[["ts", "agent", "action", "ticker", "conviction", "rationale"]],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Outcome 분석")
    if "outcome_1m" in df.columns:
        with_o = df.dropna(subset=["outcome_1m"])
        if not with_o.empty:
            attribution = (
                with_o.groupby("agent")["outcome_1m"].agg(["mean", "count"]).reset_index()
            )
            attribution.columns = ["agent", "avg_1m_return", "n"]
            st.dataframe(attribution, use_container_width=True, hide_index=True)


def _page_backtest() -> None:
    st.title("⏪ 백테스트 (Quant-only)")
    col1, col2, col3 = st.columns(3)
    start = col1.date_input("시작일", date.today() - timedelta(days=365 * 3))
    end = col2.date_input("종료일", date.today())
    rebalance = col3.selectbox("리밸런스", ["monthly", "weekly"])
    top_n = st.slider("Top-N 종목", 5, 30, 15)

    if st.button("백테스트 실행"):
        with st.spinner("실행 중…"):
            from backtest.engine import BacktestEngine

            res = BacktestEngine(
                start=start, end=end, rebalance=rebalance, top_n=top_n
            ).run()
        st.success("완료")
        m = res.metrics
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("CAGR", f"{m.cagr:.2%}")
        c2.metric("Sharpe", f"{m.sharpe:.2f}")
        c3.metric("MDD", f"{m.mdd:.2%}")
        c4.metric("Hit Ratio", f"{m.hit_ratio:.2%}")
        if not res.equity_curve.empty:
            chart = pd.DataFrame({"포트폴리오": res.equity_curve})
            if not res.benchmark_curve.empty:
                chart["KOSPI"] = res.benchmark_curve.reindex(
                    res.equity_curve.index, method="pad"
                )
            st.line_chart(chart)
        if res.trade_log:
            st.subheader("거래 로그")
            st.dataframe(pd.DataFrame(res.trade_log), use_container_width=True)


def _page_xray() -> None:
    st.title("🔬 Portfolio X-ray")
    st.caption("Live factor / sector / regime exposure of the current book")

    try:
        from broker.kis_client import KISClient
        from data.market import fetch_factor_panel
        from data.universe import sector_map
        from portfolio.position_state import all_states
    except Exception as e:
        st.error(f"Import failed: {e}")
        return

    # Live positions (from KIS) + persisted entry prices for PnL %
    try:
        state = KISClient().get_account_state()
    except Exception as e:
        st.warning(f"KIS unavailable, using last persisted state. ({e})")
        state = {"positions": {}, "prices": {}, "cash": 0, "nav": 0}

    positions = state.get("positions") or {}
    if not positions:
        st.info("No live positions to X-ray.")
        return

    smap = sector_map()
    panel = fetch_factor_panel(date.today())
    by_ticker = {r["ticker"]: r for r in panel.get("rows", [])}
    persisted = {s.ticker: s for s in all_states()}
    nav = float(state.get("nav") or 0) or sum(
        q * state["prices"].get(t, 0) for t, q in positions.items()
    )

    # Build the per-position X-ray table
    rows = []
    for ticker, qty in positions.items():
        px = state["prices"].get(ticker, 0)
        weight = (qty * px) / nav if nav > 0 else 0.0
        f = by_ticker.get(ticker, {})
        s = persisted.get(ticker)
        rows.append(
            {
                "ticker": ticker,
                "weight": weight,
                "sector": smap.get(ticker, "기타"),
                "M": f.get("momentum", 0.0),
                "V": f.get("value", 0.0),
                "Q": f.get("quality", 0.0),
                "L": f.get("lowvol", 0.0),
                "S": f.get("size", 0.0),
                "F": f.get("flow", 0.0),
                "B": f.get("breakout", 0.0),
                "pnl_%": (px / s.entry_price - 1.0) if s and s.entry_price else 0.0,
                "pyramid": s.pyramid_levels if s else 0,
            }
        )
    df = pd.DataFrame(rows).sort_values("weight", ascending=False)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Sector exposure
    st.subheader("Sector exposure")
    sec_df = df.groupby("sector")["weight"].sum().reset_index().sort_values(
        "weight", ascending=False
    )
    st.bar_chart(sec_df.set_index("sector"))

    # Weighted factor exposure of the whole book
    st.subheader("Portfolio factor exposure (weighted)")
    if df["weight"].sum() > 0:
        factors = ["M", "V", "Q", "L", "S", "F", "B"]
        weighted = {f: float((df[f] * df["weight"]).sum()) for f in factors}
        fac_df = pd.DataFrame.from_dict(weighted, orient="index", columns=["z"])
        st.bar_chart(fac_df)

    # Regime hint surfaced
    st.metric(
        "Regime hint",
        panel.get("regime_hint", "—"),
        f"KOSPI 3m mom {panel.get('kospi_mom_3m', 0):+.2%}",
    )


def _page_today() -> None:
    """Morning-report style: today's target, planned orders, imminent stops."""
    st.title("☀️ Today")
    today_d = date.today()

    try:
        from scheduler.morning_report import build_morning_report
        from scheduler.state import load_target

        loaded = load_target(today_d)
    except Exception as e:
        st.error(f"State load failed: {e}")
        return

    if loaded is None:
        st.warning(
            "No saved target yet. Click **Run research now** in the sidebar "
            "or run `mais run research`."
        )
        return

    target, extra = loaded
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Positions", len(target.positions))
    c2.metric("Cash %", f"{target.cash_weight:.0%}")
    c3.metric("Regime", str(extra.get("consensus_regime", "?")))
    c4.metric("Proposals", str(extra.get("n_proposals", "?")))

    st.subheader("Target weights (Top 25)")
    rows = sorted(
        target.positions.items(), key=lambda kv: kv[1], reverse=True
    )[:25]
    df = pd.DataFrame(rows, columns=["ticker", "weight"])
    df["weight%"] = df["weight"].map(lambda w: f"{w:.2%}")
    st.dataframe(df[["ticker", "weight%"]], use_container_width=True, hide_index=True)

    st.subheader("Imminent stops")
    try:
        from data.market import fetch_latest_prices
        from portfolio.position_state import all_states
        from portfolio.stops import evaluate_stops

        states = all_states()
        if states:
            prices = fetch_latest_prices([s.ticker for s in states])
            signals = evaluate_stops(
                {s.ticker: s.qty for s in states}, prices
            )
            if signals:
                sig_df = pd.DataFrame(
                    [
                        {
                            "ticker": s.ticker,
                            "reason": s.reason,
                            "pnl": f"{s.pnl_pct:+.2%}",
                        }
                        for s in signals
                    ]
                )
                st.dataframe(sig_df, use_container_width=True, hide_index=True)
            else:
                st.success("No stops imminent.")
        else:
            st.info("No tracked positions.")
    except Exception as e:
        st.warning(f"Stops eval failed: {e}")

    if st.button("📨 Send morning report now"):
        try:
            with st.spinner("Sending…"):
                build_morning_report(today_d)
            st.success("Sent to Slack/Telegram.")
        except Exception as e:
            st.error(f"Failed: {e}")


def _page_alerts() -> None:
    """Recent risk-guard / stop / pyramid / divergence events from the journal."""
    st.title("🚨 Alerts & Guards")
    df = _journal_recent(7)
    if df.empty:
        st.info("No journal entries in the last 7 days.")
        return

    interesting = df[
        df["action"].astype(str).str.contains(
            "INTRADAY_STOP|REPLACE|LIQUIDATE|PARAM_UPDATE",
            regex=True,
            na=False,
        )
    ]
    st.subheader(f"Last 7 days — {len(interesting)} guard / stop / param events")
    if interesting.empty:
        st.success("No alert-class events.")
    else:
        st.dataframe(
            interesting[
                ["ts", "agent", "action", "ticker", "rationale"]
            ].sort_values("ts", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Per-agent action mix (last 7d)")
    mix = (
        df.groupby(["agent", "action"]).size().unstack(fill_value=0)
    )
    if not mix.empty:
        st.bar_chart(mix)


def _page_learning() -> None:
    """Bandit + booster + A/B paper book — all the self-tuning surfaces."""
    st.title("🤖 Learning")

    st.subheader("TWAP bandit")
    try:
        from learning.bandit import latest_state

        state = latest_state()
        bandit_df = pd.DataFrame(state["arms"])
        bandit_df["seconds"] = bandit_df["seconds"].astype(str)
        st.dataframe(bandit_df, use_container_width=True, hide_index=True)
        if not bandit_df.empty:
            st.bar_chart(bandit_df.set_index("seconds")["avg_reward"])
        st.caption(f"epsilon = {state['epsilon']:.2f}")
    except Exception as e:
        st.info(f"Bandit not initialised: {e}")

    st.subheader("Consensus booster (recent attribution)")
    try:
        from learning.booster import score_agents

        scores = score_agents(180)
        if scores:
            sc_df = pd.DataFrame(
                [
                    {
                        "agent": s.agent,
                        "n_picks": s.n,
                        "avg_1m_return": s.mean_outcome_1m,
                        "predicted_alpha": s.pred_alpha,
                    }
                    for s in scores
                ]
            )
            st.dataframe(sc_df, use_container_width=True, hide_index=True)
            st.bar_chart(sc_df.set_index("agent")["predicted_alpha"])
        else:
            st.info("Not enough labelled picks yet (need ≥20 per agent).")
    except Exception as e:
        st.info(f"Booster not ready: {e}")

    st.subheader("A/B paper books")
    try:
        from orchestrator.ab_book import METHODS, evaluate_books, load_book

        for method in METHODS:
            nav = load_book(method)
            if nav.empty:
                continue
            st.markdown(f"**{method}**")
            st.line_chart(nav)

        books = evaluate_books(window_days=30)
        if books:
            ab_df = pd.DataFrame(books).T.reset_index().rename(
                columns={"index": "method"}
            )
            st.dataframe(ab_df, use_container_width=True, hide_index=True)
        else:
            st.info("No paper book history yet.")
    except Exception as e:
        st.info(f"A/B books unavailable: {e}")


def _settings_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "settings.yaml"


def _load_settings_yaml() -> dict:
    import yaml

    p = _settings_path()
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _save_settings_yaml(data: dict) -> None:
    import yaml

    p = _settings_path()
    p.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    try:
        from common import config as c

        c.load_yaml_settings.cache_clear()
    except Exception:
        pass


def _set_dotted(d: dict, dotted: str, value) -> None:
    keys = dotted.split(".")
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def _page_control() -> None:
    """Operator control panel — flags, params, kill switch, blacklist."""
    st.title("🎛️ Control panel")
    st.caption(
        "Edits below write to ``config/settings.yaml`` immediately. The "
        "running scheduler picks them up on the next phase."
    )

    settings = _load_settings_yaml()

    # ── Feature flags ────────────────────────────────────────────────
    st.subheader("Feature flags")
    flag_paths = [
        ("Hedge (defensive ETF)", "hedge.enabled"),
        ("TWAP execution", "execution.twap_enabled"),
        ("TWAP ε-greedy bandit", "execution.twap_bandit_enabled"),
        ("Intraday stops (every 30m)", "scheduler.intraday_stops_enabled"),
        ("A/B optimizer auto-rotate", "optimizer.ab_auto_rotate"),
        ("Booster auto-apply", "learning.auto_apply"),
        ("Realtime websocket stops", "websocket.enabled"),
    ]
    cols = st.columns(2)
    changed = False
    for i, (label, path) in enumerate(flag_paths):
        cur = bool(get_setting(path, False))
        new = cols[i % 2].toggle(label, value=cur, key=f"flag_{path}")
        if new != cur:
            _set_dotted(settings, path, bool(new))
            changed = True

    # ── Risk params (sliders) ────────────────────────────────────────
    st.subheader("Risk parameters")
    sliders = [
        ("Hard stop %", "risk.hard_stop_pct", 0.05, 0.20, 0.005),
        ("Trailing take %", "risk.trailing_take_pct", 0.05, 0.20, 0.005),
        ("Cash buffer min", "risk.cash_buffer_min", 0.02, 0.20, 0.01),
        ("Max single position", "risk.max_position_weight", 0.05, 0.30, 0.01),
        ("Max sector", "risk.max_sector_weight", 0.10, 0.50, 0.05),
        ("BUY drift", "execution.rebalance_buy_threshold", 0.02, 0.10, 0.005),
        ("SELL drift", "execution.rebalance_sell_threshold", 0.04, 0.20, 0.005),
    ]
    for label, path, lo, hi, step in sliders:
        cur = float(get_setting(path, lo) or lo)
        cur = max(lo, min(hi, cur))
        new = st.slider(
            label, min_value=lo, max_value=hi, value=cur, step=step, key=f"sl_{path}"
        )
        if abs(new - cur) > step / 100:
            _set_dotted(settings, path, float(new))
            changed = True

    if changed:
        _save_settings_yaml(settings)
        st.success("settings.yaml saved. Scheduler will pick up on next phase.")

    # ── Blacklist editor ─────────────────────────────────────────────
    st.subheader("Excluded tickers")
    bl_path = Path(get_env().mais_data_dir) / "excluded_tickers.json"
    current_list: list[str] = []
    if bl_path.exists():
        try:
            current_list = json.loads(bl_path.read_text(encoding="utf-8"))
        except Exception:
            current_list = []
    bl_text = st.text_area(
        "Comma- or newline-separated 6-digit codes",
        value=", ".join(current_list),
        key="bl_text",
    )
    if st.button("Save blacklist"):
        new_codes = [c.strip() for c in re.split(r"[,\n\s]+", bl_text) if c.strip()]
        new_codes = [c for c in new_codes if re.fullmatch(r"\d{6}", c)]
        bl_path.parent.mkdir(parents=True, exist_ok=True)
        bl_path.write_text(json.dumps(new_codes, ensure_ascii=False), encoding="utf-8")
        try:
            from data.universe import invalidate_universe_cache

            invalidate_universe_cache()
        except Exception:
            pass
        st.success(f"Saved {len(new_codes)} codes to {bl_path.name}")

    # ── Kill switch ──────────────────────────────────────────────────
    st.subheader("🚨 Kill switch")
    st.caption("Liquidates **every** open position at market. Irreversible.")
    cnf = st.text_input(
        "Type 'I-UNDERSTAND' to enable the button", value="", key="kill_confirm"
    )
    if st.button(
        "💣 Liquidate all positions",
        type="primary",
        disabled=(cnf != "I-UNDERSTAND"),
    ):
        try:
            from broker.kis_client import KISClient
            from common.types import Order, Side
            from memory.journal import DecisionJournal

            client = KISClient()
            state = client.get_account_state()
            positions = state.get("positions") or {}
            if not positions:
                st.info("No positions to liquidate.")
            else:
                journal = DecisionJournal()
                with st.spinner(f"Liquidating {len(positions)} positions…"):
                    for tk, qty in positions.items():
                        if qty <= 0:
                            continue
                        order = Order(
                            ticker=tk,
                            side=Side.SELL,
                            quantity=qty,
                            order_type="market",
                        )
                        result = client.place_order(order)
                        journal.record(
                            agent="kill_switch",
                            action="LIQUIDATE",
                            ticker=tk,
                            conviction=10,
                            rationale="Dashboard kill switch",
                            context={"result": result.model_dump()},
                        )
                st.error(f"Submitted SELLs for {len(positions)} tickers.")
        except Exception as e:
            st.error(f"Kill switch failed: {e}")


def _page_reflection_review() -> None:
    """Read the latest reflection report and approve / discard the patches."""
    st.title("🪞 Reflection review")

    refl_dir = Path(get_env().mais_data_dir) / "reflections"
    if not refl_dir.exists():
        st.info("No reflections directory yet.")
        return
    files = sorted(refl_dir.glob("*.md"), reverse=True)
    if not files:
        st.info("No reflection reports yet — they appear weekly on Friday 18:00.")
        return

    chosen = st.selectbox(
        "Report",
        files,
        format_func=lambda p: p.name,
    )
    body = chosen.read_text(encoding="utf-8")
    st.markdown(body)

    st.divider()
    if "## Auto-apply patches" in body:
        st.caption(
            "This report contains an Auto-apply patches block. Click below to "
            "run it through the whitelisted reflection_apply pipeline now."
        )
        if st.button("✅ Apply patches now"):
            try:
                from agents.reflection_apply import apply_reflection_patches

                with st.spinner("Applying…"):
                    out = apply_reflection_patches(chosen)
                st.success(
                    f"Applied {len(out.get('applied', []))}, "
                    f"rejected {len(out.get('rejected', []))}."
                )
                if out.get("rejected"):
                    st.warning(out["rejected"])
            except Exception as e:
                st.error(f"Apply failed: {e}")
        if st.button("🗑️ Strip patches block (keep narrative)"):
            # Patches block is appended at the end by booster/reflection
            # — strip from the heading through end-of-document.
            new_body = re.sub(
                r"\n*##\s*Auto-apply patches[\s\S]*\Z",
                "\n",
                body,
                flags=re.IGNORECASE,
            )
            chosen.write_text(new_body, encoding="utf-8")
            st.success("Patches block removed. Reloading page…")
            st.rerun()
    else:
        st.info("No patches block in this report — read-only.")


PAGES = {
    "📈 Overview": _page_overview,
    "☀️ Today": _page_today,
    "💼 Positions": _page_positions,
    "🔬 X-ray": _page_xray,
    "🤖 Learning": _page_learning,
    "🚨 Alerts": _page_alerts,
    "🧾 Journal": _page_journal,
    "⏪ Backtest": _page_backtest,
    "🎛️ Control": _page_control,
    "🪞 Reflection": _page_reflection_review,
}


def main() -> None:
    page = st.sidebar.radio("페이지", list(PAGES.keys()))
    PAGES[page]()
    st.sidebar.divider()
    st.sidebar.caption(
        f"data_dir: `{get_env().mais_data_dir}`\n\n"
        f"Build: {date.today().isoformat()}"
    )
    # Debug: dump latest target if present
    target_path = Path(get_env().mais_data_dir) / "latest_target.json"
    if target_path.exists():
        with st.sidebar.expander("Latest target (json)"):
            try:
                st.code(json.dumps(json.loads(target_path.read_text()), indent=2, ensure_ascii=False))
            except Exception:
                st.text(target_path.read_text())


if __name__ == "__main__":
    main()
