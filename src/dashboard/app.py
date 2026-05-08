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


def _render_sidebar() -> None:
    """Sidebar — global controls reused across pages.

    Defined as a function so module-load order doesn't break page-helper
    references (``_next_cron_summary`` etc are defined later in the file).
    Called from ``main()``.
    """
    with st.sidebar:
        auto_refresh = st.checkbox("자동 새로고침 (60초)", value=False)
        if auto_refresh:
            st.markdown(
                '<meta http-equiv="refresh" content="60">', unsafe_allow_html=True
            )

        st.divider()
        st.subheader("바로 실행")
        st.caption(
            "이 작업은 **이 탭을 블록**합니다. 다른 탭을 열고 같이 모니터링하세요."
        )

        if st.button("⏯ 지금 리서치 실행", use_container_width=True):
            try:
                from scheduler.daily_pipeline import research_phase

                with st.spinner("4개 에이전트 실행 중 (~60-120초)…"):
                    research_phase()
                st.success("리서치 완료 — Today 페이지로 이동.")
            except Exception as e:
                _show_friendly_error(e, context="리서치 실행 실패")

        if st.button("📅 EOD 정산 실행", use_container_width=True):
            try:
                from scheduler.daily_pipeline import eod_phase

                with st.spinner("EOD 정산 중 (~5-10초)…"):
                    eod_phase()
                st.success("EOD 정산 완료.")
            except Exception as e:
                _show_friendly_error(e, context="EOD 정산 실패")

        st.divider()
        import contextlib

        with contextlib.suppress(Exception):
            st.caption(f"⏰ {_next_cron_summary()}")


def _pct(v: float) -> str:
    """Color-coded HTML span for a +/- percentage."""
    color = "#22c55e" if v >= 0 else "#dc2626"
    return f'<span style="color:{color};font-weight:600">{v:+.2%}</span>'


# =====================================================================
# Friendly error / onboarding helpers
# =====================================================================
_ERROR_HINTS: dict[str, str] = {
    "KIS_APP_KEY": (
        "KIS 인증이 비어있습니다. ``.env`` 파일에 ``KIS_APP_KEY`` / "
        "``KIS_APP_SECRET`` / ``KIS_ACCOUNT_NO`` 를 채우거나 "
        "``mais init`` 마법사를 실행하세요."
    ),
    "RetryError": (
        "KIS 서버 호출이 실패했습니다. 네트워크 또는 KIS 키가 만료되었을 수 "
        "있습니다. 터미널에서 ``mais doctor`` 로 점검하세요."
    ),
    "ANTHROPIC_API_KEY": (
        "ANTHROPIC API 키가 없습니다. LLM 에이전트가 동작하려면 ``.env`` 의 "
        "``ANTHROPIC_API_KEY`` 를 채워야 합니다."
    ),
    "No module named 'pykrx'": (
        "데이터 패키지가 설치되어 있지 않습니다. Docker 이미지에서는 자동 "
        "설치되지만, 로컬 환경에선 ``pip install pykrx FinanceDataReader yfinance`` "
        "를 실행하세요."
    ),
    "no_proposals_met_min_conviction": (
        "에이전트들이 충분한 신뢰도로 의견을 내지 못해 합의가 이루어지지 "
        "못했습니다. ``mais run research`` 를 다시 실행하거나 "
        "``consensus.min_conviction`` 을 임시로 낮춰보세요."
    ),
}


def _friendly_error(exc: Exception) -> str:
    """Translate a raw exception into a Korean operator-friendly hint."""
    msg = str(exc)
    for needle, hint in _ERROR_HINTS.items():
        if needle in msg or needle in repr(exc):
            return hint
    return f"오류: `{type(exc).__name__}: {msg[:200]}`"


def _show_friendly_error(exc: Exception, context: str = "") -> None:
    """Render a friendly error block in the current Streamlit page."""
    hint = _friendly_error(exc)
    st.error(f"⚠️ {context}\n\n{hint}" if context else f"⚠️ {hint}")
    with st.expander("자세히 보기 (raw)"):
        st.code(f"{type(exc).__name__}: {exc}")


def _last_updated(label: str = "마지막 갱신") -> None:
    """Right-aligned timestamp showing when this page rendered."""
    from datetime import datetime as _dt

    st.caption(
        f"<div style='text-align:right;opacity:0.7'>{label}: "
        f"{_dt.now().strftime('%H:%M:%S')}</div>",
        unsafe_allow_html=True,
    )


def _onboarding_cta(message: str, button_label: str, action: str) -> None:
    """Big call-to-action when a page has no data yet.

    ``action`` ∈ {'research', 'doctor', 'init'} — maps to the right
    follow-up.
    """
    st.info(message)
    if st.button(f"▶ {button_label}", type="primary"):
        try:
            if action == "research":
                from scheduler.daily_pipeline import research_phase

                with st.spinner("리서치 실행 중 (~60-120초)…"):
                    research_phase()
                st.success("리서치 완료. 페이지를 새로고침하세요.")
                st.rerun()
            elif action == "doctor":
                st.info(
                    "터미널에서 ``mais doctor`` 를 실행하세요. 환경변수 + 패키지 "
                    "설치 + 거래일 여부를 한 번에 점검합니다."
                )
            elif action == "init":
                st.info("터미널에서 ``mais init`` 을 실행하세요.")
        except Exception as e:
            _show_friendly_error(e, context="실행 실패")


def _next_cron_summary() -> str:
    """Compute time until the next scheduled phase. Returns a friendly
    one-liner like '다음 리서치까지 1시간 23분'."""
    from datetime import datetime as _dt

    now = _dt.now()
    crons = [
        ("리서치", str(get_setting("scheduler.research_time", "08:00"))),
        ("주문", str(get_setting("scheduler.order_time", "09:05"))),
        ("EOD", str(get_setting("scheduler.eod_review_time", "16:00"))),
    ]
    upcoming: list[tuple[str, _dt]] = []
    for name, hhmm in crons:
        try:
            h, m = map(int, hhmm.split(":"))
        except (ValueError, AttributeError):
            continue
        when = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if when <= now:
            from datetime import timedelta as _td

            when += _td(days=1)
        upcoming.append((name, when))
    if not upcoming:
        return "다음 일정 없음"
    name, when = min(upcoming, key=lambda kv: kv[1])
    delta = when - now
    h = delta.seconds // 3600
    m = (delta.seconds % 3600) // 60
    return f"다음 [{name}] 까지 {h}시간 {m}분"


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
        _onboarding_cta(
            "아직 저널 기록이 없습니다. 첫 리서치를 실행해서 시스템을 시작하세요.",
            "지금 첫 리서치 실행",
            "research",
        )
        _last_updated()
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
    _last_updated()


def _page_positions() -> None:
    st.title("💼 현재 포지션")
    try:
        from broker.kis_client import KISClient
        from portfolio.position_state import all_states

        state = KISClient().get_account_state()
        states = {s.ticker: s for s in all_states()}
    except Exception as e:
        _show_friendly_error(e, context="KIS 잔고 조회 실패")
        return
    if not state["positions"]:
        st.info(
            "보유 포지션이 없습니다. "
            "사이드바의 **Run research now** 로 첫 타깃을 만든 뒤, "
            "**mais run order --live** 또는 09:05 cron으로 매수가 발생합니다."
        )
        _last_updated()
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
    if not df.empty:
        st.download_button(
            "⬇ CSV 내려받기",
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"positions_{date.today().isoformat()}.csv",
            mime="text/csv",
        )
    _last_updated()


def _page_journal() -> None:
    st.title("🧾 의사결정 저널")
    days = st.slider(
        "조회 기간 (days)",
        min_value=1,
        max_value=180,
        value=30,
        help="저널에서 최근 며칠 어치를 가져올지 — 길수록 attribution이 안정적",
    )
    df = _journal_recent(days)
    if df.empty:
        _onboarding_cta(
            "조회된 저널 기록이 없습니다. 첫 리서치를 돌려보세요.",
            "지금 첫 리서치 실행",
            "research",
        )
        _last_updated()
        return

    # Filters row
    f1, f2, f3 = st.columns(3)
    agent = f1.selectbox(
        "Agent", ["(전체)", *sorted(df["agent"].unique().tolist())]
    )
    action = f2.selectbox(
        "Action",
        ["(전체)", *sorted(df["action"].astype(str).unique().tolist())],
    )
    ticker_q = f3.text_input("Ticker / 키워드", help="6자리 코드 또는 부분 텍스트")

    if agent != "(전체)":
        df = df[df["agent"] == agent]
    if action != "(전체)":
        df = df[df["action"] == action]
    if ticker_q:
        q = ticker_q.strip()
        df = df[
            df["ticker"].astype(str).str.contains(q, na=False, regex=False)
            | df["rationale"].astype(str).str.contains(q, na=False, regex=False)
        ]

    st.caption(f"행 수: **{len(df)}**")
    cols_to_show = [c for c in ["ts", "agent", "action", "ticker", "conviction",
                                "rationale", "outcome_1w", "outcome_1m"] if c in df.columns]
    st.dataframe(
        df[cols_to_show],
        use_container_width=True,
        hide_index=True,
    )

    # CSV export
    if not df.empty:
        st.download_button(
            "⬇ CSV 내려받기",
            data=df[cols_to_show].to_csv(index=False).encode("utf-8-sig"),
            file_name=f"journal_{date.today().isoformat()}.csv",
            mime="text/csv",
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
        else:
            st.caption("outcome 데이터가 아직 backfill되지 않았습니다 (1주~1개월 후 자동 채워짐).")
    _last_updated()


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
        st.warning(
            "KIS 잔고 조회 실패 — 마지막 저장 상태로 표시합니다.\n\n"
            f"{_friendly_error(e)}"
        )
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
        _show_friendly_error(e, context="상태 파일 로드 실패")
        return

    if loaded is None:
        _onboarding_cta(
            f"오늘({today_d.isoformat()})자 타깃이 아직 없습니다. "
            "리서치를 실행해서 오늘의 포트폴리오 타깃을 만들어보세요.",
            "지금 첫 리서치 실행",
            "research",
        )
        _last_updated()
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

    if st.button("📨 모닝리포트 즉시 발송"):
        try:
            with st.spinner("발송 중…"):
                build_morning_report(today_d)
            st.success("Slack/Telegram 발송 완료.")
        except Exception as e:
            _show_friendly_error(e, context="모닝리포트 발송 실패")
    _last_updated()


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
    _last_updated()


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
    _last_updated()


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
    st.subheader("기능 플래그")
    flag_paths = [
        (
            "방어 자산 헷지 (Hedge)",
            "hedge.enabled",
            "regime이 risk_off일 때 cash의 일부를 금 ETF/단기자금/장기채로 자동 분배",
        ),
        (
            "TWAP 분할 체결",
            "execution.twap_enabled",
            "주문을 4분할로 시간 분산해서 시장 충격 완화",
        ),
        (
            "TWAP ε-greedy 밴딧",
            "execution.twap_bandit_enabled",
            "TWAP 간격을 매일 강화학습으로 자동 튜닝 (slippage 기반)",
        ),
        (
            "장중 stops (30분 간격)",
            "scheduler.intraday_stops_enabled",
            "10:00~15:00 매 30분마다 stops만 평가 (추가매수 X)",
        ),
        (
            "A/B 옵티마이저 자동 회전",
            "optimizer.ab_auto_rotate",
            "월말에 paper book Sharpe 우승 옵티마이저로 자동 전환",
        ),
        (
            "Booster 자동 적용",
            "learning.auto_apply",
            "리플렉션 검토 없이 ML booster의 가중치 패치를 즉시 반영",
        ),
        (
            "실시간 웹소켓 stops",
            "websocket.enabled",
            "KIS H0STCNT0 구독 — 1.5% 즉시 변동에 stops 발동 (paper에서 1주 검증 후 권장)",
        ),
    ]
    cols = st.columns(2)
    changed = False
    for i, (label, path, help_text) in enumerate(flag_paths):
        cur = bool(get_setting(path, False))
        new = cols[i % 2].toggle(
            label, value=cur, key=f"flag_{path}", help=help_text
        )
        if new != cur:
            _set_dotted(settings, path, bool(new))
            changed = True

    # ── Risk params (sliders) ────────────────────────────────────────
    st.subheader("리스크 파라미터")
    sliders = [
        (
            "하드 손절 (Hard stop)",
            "risk.hard_stop_pct",
            0.05, 0.20, 0.005,
            "진입가 대비 이만큼 빠지면 무조건 시장가 매도 (기본 12%)",
        ),
        (
            "트레일링 익절 (Trailing take)",
            "risk.trailing_take_pct",
            0.05, 0.20, 0.005,
            "고점 대비 이만큼 빠지면 익절 (단, 진입 +5% 이후만 발동)",
        ),
        (
            "최소 현금 보유",
            "risk.cash_buffer_min",
            0.02, 0.20, 0.01,
            "포트폴리오의 최소 현금 비중. 너무 낮으면 슬리피지 + 비상 대응 어려움",
        ),
        (
            "단일 종목 최대 비중",
            "risk.max_position_weight",
            0.05, 0.30, 0.01,
            "한 종목에 NAV의 몇 %까지 허용할지 (기본 10%)",
        ),
        (
            "단일 섹터 최대 비중",
            "risk.max_sector_weight",
            0.10, 0.50, 0.05,
            "한 섹터(반도체 등)에 NAV의 몇 %까지 허용할지 (기본 30%)",
        ),
        (
            "매수 drift 임계값",
            "execution.rebalance_buy_threshold",
            0.02, 0.10, 0.005,
            "현재/타깃 차이가 이 % 이상이면 BUY 주문 발생 (낮을수록 자주 매수)",
        ),
        (
            "매도 drift 임계값",
            "execution.rebalance_sell_threshold",
            0.04, 0.20, 0.005,
            "비대칭: 매수보다 높게 둬서 '가는 말 타고 오래' 원칙 유지",
        ),
    ]
    for label, path, lo, hi, step, help_text in sliders:
        cur = float(get_setting(path, lo) or lo)
        cur = max(lo, min(hi, cur))
        new = st.slider(
            label,
            min_value=lo,
            max_value=hi,
            value=cur,
            step=step,
            key=f"sl_{path}",
            help=help_text,
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
        st.info("이 리포트에는 자동 적용 patches 블록이 없습니다 — 읽기 전용.")
    _last_updated()


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
    _render_sidebar()
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
