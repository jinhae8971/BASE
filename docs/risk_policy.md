# Risk Policy

Non-negotiable limits enforced in code (`src/agents/execution_agent.py`, `src/orchestrator/optimizer.py`).

## Portfolio-level limits

| Limit | Value | Enforced in |
|---|---|---|
| Single position weight | ≤ **10%** | `PortfolioOptimizer`, `ExecutionAgent` |
| Single sector weight | ≤ **30%** | `PortfolioOptimizer._enforce_sector_cap` |
| Minimum cash buffer | ≥ **5%** | `PortfolioOptimizer` |
| Portfolio MDD trigger | **-15%** → de-leverage | (Phase 3) daily guard |
| Daily loss kill | **-3%** → halt new orders | (Phase 3) daily guard |
| Max daily turnover | **30%** | (Phase 3) daily guard |
| Rebalance threshold | 5% drift | `ExecutionAgent._plan_orders` |

## Order-level safeguards

- Orders below the rebalance threshold are skipped (reduces turnover, costs, and noise).
- BUY orders are scaled down if total cost exceeds available cash.
- Execution defaults to **dry-run**; the pipeline only submits real orders when invoked with `--live` and `KIS_ENV=live`.
- All orders flow through `KISClient.place_order`, which catches and logs every exception as `rejected`.

## Emergency Response

- `scripts/kill_switch.py --confirm I-UNDERSTAND`: market-sells every holding, records the action to the journal.
- Secrets leakage: rotate `KIS_APP_KEY` and `KIS_APP_SECRET` immediately via the KIS portal; invalidate the token cache at `.kis_token.json`.
- Runaway agents: set `KIS_ENV=paper` in `.env` and restart the scheduler.

## Loosening Limits

Changes to any of the above require:
1. Backtest showing the new limit **preserves or improves** the MDD target.
2. Paper-traded verification for ≥ 2 weeks.
3. PR description documenting the trade-off against **CAGR, MDD, Sharpe, IR**.
4. Human approval.

## Regulatory Notes

This system is for **personal research use only**. Korean financial regulation restricts investment advisory and automated trading for third parties; do **not** operate this system on behalf of another person without appropriate licensing. Nothing in this repo constitutes investment advice.
