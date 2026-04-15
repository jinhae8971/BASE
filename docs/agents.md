# Agents

## Contract

Every specialist agent subclasses `BaseAgent` and implements:

```python
class BaseAgent(ABC):
    name: str
    prompt_file: str

    def gather_context(self, as_of: date) -> dict: ...
    def parse_response(self, text: str, as_of: date) -> AgentProposal: ...
```

`run(as_of)` is the common entry point that:
1. calls `gather_context` for research data,
2. loads the system prompt from `config/prompts/<name>.md`,
3. invokes Claude with caching on the system prompt,
4. parses the response into an `AgentProposal`.

## Roster

### 🌍 MacroAgent — regime & equity weight
- **Inputs**: rates (KR 3Y, US 10Y, real rates), FX (USD/KRW, DXY), commodities, equity momentum, credit spreads, leading indicators, scheduled events
- **Output**: `regime ∈ {risk_on, neutral, risk_off}`, `equity_weight ∈ [0,1]`, conviction, rationale

### 🏭 SectorAgent — sector rotation
- **Inputs**: sector momentum, PER/PBR, earnings revision, news sentiment
- **Output**: `sector_tilts` (±) summing to ~0

### 💎 ValueAgent — fundamentals-driven picks
- **Inputs**: DART financials, multiples, ROE/ROIC, DCF intrinsic value, dividends
- **Output**: up to 10 picks with `target_weight`, `score`, rationale

### 📊 QuantAgent — factor ranking
- **Inputs**: cross-sectional z-scores for Value / Momentum / Quality / LowVol / Size
- **Output**: up to 15 picks with composite scores — strictly numeric

### ⚙️ ExecutionAgent — risk-gated order placement
- Not primarily an LLM. Deterministic order planner + optional LLM sanity check.
- Applies position / sector / cash / turnover caps.
- Submits via `KISClient.place_order`.

### 🪞 ReflectionAgent — weekly improvement proposals
- Reads 30-day window from the Decision Journal
- Produces a Markdown report in `data_store/reflections/YYYY-MM-DD.md`
- Human applies accepted changes to `config/prompts/` or `config/settings.yaml`

## Proposal Schema

See `src/common/types.py::AgentProposal`. All specialists must output:

```python
AgentProposal(
    agent_name: str,
    as_of: date,
    conviction: int (0-10),
    rationale: str,
    equity_weight: float | None,   # macro only
    regime: MarketRegime | None,    # macro only
    sector_tilts: dict[str, float], # sector only
    picks: list[TickerView],        # value/quant
    context_used: dict,
)
```

## Prompt Engineering Guidelines

- Keep system prompts version-controlled under `config/prompts/`.
- Each prompt enforces **strict JSON output** (or Markdown for reflection).
- Use prompt caching on the system block — the user block is the only thing that changes daily.
- Never include API keys or account numbers in any prompt context.
- Temperature:
  - Macro/Sector/Value: 0.1–0.2
  - Quant: 0.0 (deterministic)
  - Reflection: 0.3 (slight creativity OK for diagnosis)
