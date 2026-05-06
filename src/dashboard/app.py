"""MAIS Streamlit Dashboard — portfolio monitor and decision journal viewer.

Run:
    streamlit run src/dashboard/app.py
or via Docker (port 8501).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import get_setting
from common.logging import setup_logging
from memory.journal import DecisionJournal

setup_logging()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="MAIS Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 MAIS — Multi-Agent Investment System")
st.caption("Korean Equity Automated Trading · KOSPI Benchmark")

# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("설정")
    lookback = st.slider("조회 기간 (일)", min_value=7, max_value=90, value=30, step=7)
    agent_filter = st.multiselect(
        "에이전트 필터",
        ["macro", "sector", "value", "quant", "execution", "reflection"],
        default=[],
    )
    st.divider()
    st.caption(f"Journal: `{get_setting('memory.journal_db', 'data_store/journal.sqlite')}`")
    if st.button("새로고침"):
        st.cache_data.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_decisions(days: int) -> list[dict]:
    try:
        return DecisionJournal().recent(days)
    except Exception:
        return []


decisions = load_decisions(lookback)

# Apply agent filter
if agent_filter:
    decisions = [d for d in decisions if d.get("agent") in agent_filter]

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("총 의사결정", len(decisions))

if decisions:
    convictions = [d.get("conviction") or 0 for d in decisions]
    col2.metric("평균 확신도", f"{sum(convictions)/len(convictions):.1f} / 10")
    agents_seen = {d.get("agent") for d in decisions}
    col3.metric("활성 에이전트", len(agents_seen))
    last_ts = decisions[0].get("ts", "N/A")[:16].replace("T", " ")
    col4.metric("최근 실행", last_ts)
else:
    col2.metric("평균 확신도", "—")
    col3.metric("활성 에이전트", "—")
    col4.metric("최근 실행", "없음")

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_journal, tab_agents, tab_positions, tab_backtest = st.tabs(
    ["📋 의사결정 저널", "🤖 에이전트 분석", "💼 포지션", "📊 백테스트"]
)


# ── Journal tab ──────────────────────────────────────────────────────────────
with tab_journal:
    if not decisions:
        st.info("아직 의사결정 기록이 없습니다. 파이프라인을 실행하세요.")
        st.code("docker compose exec scheduler python -m scheduler.daily_pipeline --dry-run")
    else:
        df = pd.DataFrame(decisions)
        df["timestamp"] = df["ts"].str[:16].str.replace("T", " ")

        # Conviction over time
        st.subheader("확신도 추이")
        fig = go.Figure()
        for agent in df["agent"].unique():
            sub = df[df["agent"] == agent].sort_values("ts")
            fig.add_trace(
                go.Scatter(
                    x=sub["timestamp"],
                    y=sub["conviction"],
                    mode="lines+markers",
                    name=agent,
                )
            )
        fig.update_layout(
            yaxis_title="Conviction (0-10)",
            xaxis_title="시간",
            height=350,
            margin=dict(l=20, r=20, t=20, b=20),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Decision table
        st.subheader("의사결정 목록")
        display_cols = ["timestamp", "agent", "action", "ticker", "conviction", "rationale"]
        available = [c for c in display_cols if c in df.columns]
        st.dataframe(
            df[available].head(50),
            use_container_width=True,
            hide_index=True,
        )


# ── Agent analysis tab ───────────────────────────────────────────────────────
with tab_agents:
    if not decisions:
        st.info("의사결정 데이터가 없습니다.")
    else:
        df = pd.DataFrame(decisions)

        col_a, col_b = st.columns(2)

        with col_a:
            st.subheader("에이전트별 의사결정 수")
            agent_counts = df["agent"].value_counts().reset_index()
            agent_counts.columns = ["agent", "count"]
            fig_bar = go.Figure(
                go.Bar(x=agent_counts["agent"], y=agent_counts["count"],
                       marker_color="#4F8EF7")
            )
            fig_bar.update_layout(height=300, margin=dict(l=20, r=20, t=20, b=20))
            st.plotly_chart(fig_bar, use_container_width=True)

        with col_b:
            st.subheader("에이전트별 평균 확신도")
            avg_conv = (
                df.groupby("agent")["conviction"].mean().reset_index()
            )
            avg_conv.columns = ["agent", "avg_conviction"]
            avg_conv["avg_conviction"] = avg_conv["avg_conviction"].round(2)
            st.dataframe(avg_conv, use_container_width=True, hide_index=True)

        # Ticker mentions
        st.subheader("종목 언급 빈도")
        ticker_df = df[df["ticker"].notna() & (df["ticker"] != "")]
        if not ticker_df.empty:
            tc = ticker_df["ticker"].value_counts().head(15).reset_index()
            tc.columns = ["ticker", "mentions"]
            fig_t = go.Figure(
                go.Bar(x=tc["ticker"], y=tc["mentions"], marker_color="#27AE60")
            )
            fig_t.update_layout(height=300, margin=dict(l=20, r=20, t=20, b=20))
            st.plotly_chart(fig_t, use_container_width=True)
        else:
            st.info("종목 언급 데이터 없음")


# ── Positions tab ────────────────────────────────────────────────────────────
with tab_positions:
    st.subheader("최근 포트폴리오 타겟")
    st.info(
        "실시간 포지션은 KIS 계좌와 연동됩니다. "
        "파이프라인 실행 후 최근 컨텍스트에서 포지션 정보를 확인하세요."
    )

    # Show latest portfolio target from journal context
    if decisions:
        df = pd.DataFrame(decisions)
        exec_rows = df[df["agent"] == "execution"]
        if not exec_rows.empty and "context_json" in exec_rows.columns:
            latest = exec_rows.iloc[0]
            try:
                ctx = json.loads(latest["context_json"] or "{}")
                target = ctx.get("target", {})
                positions = target.get("positions", {})
                if positions:
                    pos_df = pd.DataFrame(
                        [{"ticker": k, "weight": f"{v*100:.1f}%"} for k, v in positions.items()]
                    )
                    col_p1, col_p2 = st.columns([2, 1])
                    col_p1.dataframe(pos_df, use_container_width=True, hide_index=True)
                    col_p2.metric("현금 비중", f"{target.get('cash_weight', 0)*100:.1f}%")
                    col_p2.metric("주식 비중", f"{target.get('equity_weight', 0)*100:.1f}%")
            except Exception:
                st.warning("포지션 컨텍스트를 파싱할 수 없습니다.")


# ── Backtest tab ──────────────────────────────────────────────────────────────
with tab_backtest:
    st.subheader("KOSPI200 인덱스 백테스트")

    col_bt1, col_bt2, col_bt3 = st.columns(3)
    bt_start = col_bt1.date_input("시작일", value=pd.Timestamp("2020-01-01").date())
    bt_end = col_bt2.date_input("종료일", value=pd.Timestamp.today().date())
    bt_mode = col_bt3.selectbox("모드", ["index (KOSPI200)", "equal_weight (top-30)"])

    if st.button("백테스트 실행", type="primary"):
        with st.spinner("pykrx에서 데이터를 가져오는 중... (수십 초 소요될 수 있음)"):
            try:
                from backtest.engine import BacktestEngine

                eng = BacktestEngine(start=bt_start, end=bt_end)
                if "equal_weight" in bt_mode:
                    result = eng.run_equal_weight()
                else:
                    result = eng.run()

                m = result.metrics
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("CAGR", f"{m.cagr*100:.2f}%")
                c2.metric("MDD", f"{m.max_drawdown*100:.2f}%")
                c3.metric("Sharpe", f"{m.sharpe:.3f}")
                c4.metric("Hit Rate", f"{m.hit_ratio*100:.1f}%")

                # Equity curve
                fig_eq = go.Figure()
                fig_eq.add_trace(
                    go.Scatter(
                        x=result.equity_curve.index,
                        y=result.equity_curve.values,
                        mode="lines",
                        name="Portfolio",
                        line=dict(color="#4F8EF7", width=2),
                    )
                )
                fig_eq.update_layout(
                    title="자산 곡선 (KRW)",
                    yaxis_title="가치 (원)",
                    xaxis_title="날짜",
                    height=400,
                    hovermode="x unified",
                )
                st.plotly_chart(fig_eq, use_container_width=True)

            except Exception as exc:
                st.error(f"백테스트 실패: {exc}")
