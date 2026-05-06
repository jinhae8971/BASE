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
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("KIS 환경", env.kis_env.upper())
    col2.metric("벤치마크", str(get_setting("system.benchmark", "KOSPI")))
    col3.metric("MDD 한도", f"{get_setting('risk.max_portfolio_mdd', 0.15):.0%}")
    col4.metric("종목당 한도", f"{get_setting('risk.max_position_weight', 0.10):.0%}")

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

        state = KISClient().get_account_state()
    except Exception as e:
        st.error(f"KIS 잔고 조회 실패: {e}")
        return
    if not state["positions"]:
        st.info("보유 포지션이 없습니다.")
        return
    rows = [
        {"ticker": t, "qty": q, "price": state["prices"].get(t, 0)}
        for t, q in state["positions"].items()
    ]
    df = pd.DataFrame(rows)
    df["value"] = df["qty"] * df["price"]
    st.dataframe(df, use_container_width=True, hide_index=True)
    col1, col2 = st.columns(2)
    col1.metric("현금 (KRW)", f"{state['cash']:,.0f}")
    col2.metric("순자산 (KRW)", f"{state['nav']:,.0f}")


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


PAGES = {
    "📈 Overview": _page_overview,
    "💼 Positions": _page_positions,
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
