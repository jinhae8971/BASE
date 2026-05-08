"""Streamlit operational dashboard.

Run inside the Docker stack via the ``dashboard`` service:

    docker compose up dashboard

Standalone:

    streamlit run src/dashboard/app.py

Pages:
    1. Overview        — live equity curve, today's target, agent proposals
    2. Positions       — current KIS holdings (or last known)
    3. Decision Log    — searchable journal of every agent decision
    4. Backtest        — quant-only historical simulation
"""
from __future__ import annotations

import json
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
        # Lightweight refresh — 60s. streamlit-autorefresh would be cleaner
        # but we avoid the extra dependency.
        import time as _t

        _t.sleep(0)  # placeholder; meta refresh handles the actual reload
        st.markdown(
            '<meta http-equiv="refresh" content="60">', unsafe_allow_html=True
        )

    st.divider()
    st.subheader("Quick actions")

    if st.button("⏯ Run research now", use_container_width=True):
        try:
            from scheduler.daily_pipeline import research_phase

            with st.spinner("Running research…"):
                research_phase()
            st.success("Research finished — see Today page.")
        except Exception as e:
            st.error(f"research failed: {e}")

    if st.button("📅 EOD reconcile now", use_container_width=True):
        try:
            from scheduler.daily_pipeline import eod_phase

            with st.spinner("Running EOD…"):
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


PAGES = {
    "📈 Overview": _page_overview,
    "💼 Positions": _page_positions,
    "🔬 X-ray": _page_xray,
    "🧾 Journal": _page_journal,
    "⏪ Backtest": _page_backtest,
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
