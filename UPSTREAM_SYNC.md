# Upstream Sync Guide

**Our fork**: `SpookyFox9/TradingAgents` (branch `main`)  
**Upstream**: `TauricResearch/TradingAgents` (remote alias `upstream`)  
**Weekly check**: `check_upstream.ps1` in StockBoy root — Slack alert every Sunday 8 AM if new commits exist

We selectively cherry-pick upstream improvements. We do **not** merge wholesale — our
`portfolio_lib/`, `analyze_portfolio.py`, and compliance layer are not in upstream and would
be overwritten by a naïve merge.

---

## Quick Reference: Triage Decision

```
upstream commit
    │
    ├─ matches SKIP LIST?          → skip, no review
    ├─ touches only SAFE ZONES?    → cherry-pick (expect clean)
    ├─ touches CAUTION ZONES?      → read the diff, apply manually if worth it
    └─ touches OUR LAYER?          → skip or coordinate
```

---

## Safe-Take Zones
Cherry-picks almost always apply cleanly here:

| Path | What |
|------|------|
| `tradingagents/agents/analysts/` | Analyst prompt logic |
| `tradingagents/agents/researchers/` | Researcher prompts |
| `tradingagents/agents/managers/` | Portfolio/Research manager prompts |
| `tradingagents/agents/trader/` | Trader agent |
| `tradingagents/dataflows/` | Data fetching, yfinance, indicators |
| `tradingagents/llm_clients/anthropic_client.py` | Our LLM provider |
| `tradingagents/default_config.py` | Config defaults + model catalog |
| `pyproject.toml` | Dependencies |

## Caution Zones
We've modified these — diff carefully before applying:

| Path | Why |
|------|-----|
| `tradingagents/graph/trading_graph.py` | We added `safe_ticker_component`, `resolve_instrument_context`, `_fetch_returns(benchmark=)` — heavy divergence from upstream |
| `tradingagents/graph/propagation.py` | Upstream adds `asset_type`/`instrument_context` params; we keep our `create_initial_state` signature |
| `tradingagents/agents/utils/agent_utils.py` | We added `resolve_instrument_identity`, `build_instrument_context`, `get_instrument_context_from_state` in Phase 4 |
| `tradingagents/agents/utils/memory.py` | `TradingMemoryLog` — fully migrated in Phase 3; diff carefully before taking upstream changes |
| `tests/conftest.py` | Upstream added autouse config-isolation fixture (Phase 4); we have API key stubs — check for fixture name collisions |

## Our Layer — Never Overwrite
These files don't exist in upstream:

- `portfolio_lib/` (entire directory — compliance, executor, loader, alpaca_sync, etc.)
- `analyze_portfolio.py`
- `approve_trades.py`
- `tests/test_compliance.py`, `test_executor_compliance.py`, `test_alpaca_sync.py`, `test_alpaca_sync.py`

---

## Permanent Skip List

Anything matching these patterns — skip with no further review:

| Pattern | Reason |
|---------|--------|
| Ollama, OpenRouter, DeepSeek, Qwen, GLM, MiniMax, Azure, xAI, grok, Bedrock, NIM, Kimi, Groq, Mistral | Providers we don't use |
| Docker | Not our deployment model |
| CLI dropdown, `.env` persistence | We use `run_analysis.ps1` + Windows User env vars |
| i18n, localize, multi-language | English only |
| crypto, `asset_type` routing | Equities only |
| `api.tauric.ai` announcements | Tauric SaaS feature |
| FRED, Polymarket | Macro/prediction vendors we don't use |
| China A-share benchmarks, non-US tickers | US equities only |
| Reddit, StockTwits, sentiment_analyst | Quality preset doesn't run these |

## Fast-Track Patterns
Worth picking up immediately:

| Pattern | Why |
|---------|-----|
| `fix: encoding`, `UTF-8` | Windows cp1252 crashes |
| `fix(security)`, CVE | Always review |
| `fix:` + `report` or `save` | Affects our analysis output |
| `fix(llm): …Anthropic` | Our exclusive provider |
| `yfinance` bugfixes | Affects data quality |
| Claude model catalog bumps | We use Claude only |

---

## Pending Phases

### Phase 1 — Easy wins ✅ DONE (2026-05-30)
```
afdc6d4  chore: suppress upstream langgraph allowed_objects deprecation noise
8e7654f  fix: drop past-memory directive and placeholder from agent prompts when memory is empty
```

### Phase 2 — Structured Output ✅ DONE (2026-05-30)
```
0fda245  feat: structured-output Portfolio Manager + 5-tier rating consistency
bba1477  feat: structured-output Trader and Research Manager
```
All three agents (PM, RM, Trader) now use `bind_structured` / `invoke_structured_or_freetext`.
`memory` parameter kept as optional (defaults to `None`) for BM25 compat until Phase 3.

### Phase 3 — Memory Migration ✅ DONE (2026-05-30)
```
ebd2e12  feat: replace per-agent BM25 memory with persistent decision log
```
Removed `rank-bm25`. All 5 BM25 stores replaced by `TradingMemoryLog` — a single
append-only markdown log at `~/.tradingagents/memory/trading_memory.md`.  
`memory_seed.py` rewritten as `build_doctrine_context()` — injects GARP/barbell rules
and graded past signals as prose via `config["doctrine_context"]` on every run.  
Deferred reflection: returns fetched from yfinance after each run; deep-thinking LLM
writes 2-4 sentence reflection, stored alongside the decision for future runs.

### Phase 4 — Data quality + instrument identity ✅ DONE (2026-06-21)

**Data layer (safe zone):**
```
a66aa8f  fix(deps): require yfinance >=1.4.1 and tolerate non-Date index column
dab0768  fix(data): include the requested end date in yfinance fetches
0c1231a  fix(data): keep future/undated news out of historical windows
e4be7cc  fix(data): add Alpha Vantage request timeout and stop mislabeling bad keys
1ff3f07  fix: support commodity/forex/crypto tickers and never invent prices  ← dependency only; adds symbol_utils.py + NoMarketDataError
6560883  fix(data): respect the configured vendor chain and log vendor failures
9fd54f8  fix(data): reject stale yfinance OHLCV instead of reporting wrong prices
7df18fc  refactor(data): unify vendor errors under a VendorError hierarchy
```

**Graph/identity layer (caution zone):**
```
d7b40a2  fix(graph): resolve instrument identity to stop wrong-company hallucination
47cbb32  feat(market): verified market-data snapshot to ground numeric claims
7c8fe2f  fix(data): normalize symbols on the identity and reflection paths
4e7821d  fix(graph): register get_verified_market_snapshot in the market ToolNode
```

**Conflict resolutions to remember for next sync:**
- `pyproject.toml`: keep `alpaca-py>=0.43.0` alongside upstream's yfinance bump + pytest/ruff sections
- `interface.py`: skip FRED + Polymarket imports; `NoMarketDataError` now comes from `.errors`, not `.symbol_utils`
- `trading_graph.py`: kept SPY-default benchmark in `_fetch_returns` but added `benchmark` param for test compat
- All agent files: `instrument_context` injected via `get_instrument_context_from_state(state)` pattern
- `1ff3f07` is a required dependency (provides `symbol_utils.py`) — always pull it before `6560883`/`7c8fe2f`/`d7b40a2`

Test count after sync: 301 passing, 1 pre-existing failure (`test_is_blocked_ticker_wash_sale_lockout_with_portfolio`).

---

## Process Commands

```powershell
# Fetch (never merges)
git fetch upstream

# See what's new
git log origin/main..upstream/main --oneline

# Inspect a specific commit
git show <sha> --stat        # files changed
git show <sha>               # full diff

# Apply
git cherry-pick <sha>

# On conflict: resolve, then
git cherry-pick --continue --no-edit
# Or skip if it's already applied
git cherry-pick --skip

# Verify
.\.venv\Scripts\python.exe -m pytest tests/ -v

# Push
git push
```

---

## Sync History

| Date | SHAs taken | Notes |
|------|-----------|-------|
| 2026-05-30 | `872b063` `2c97bad` `9482cae` `c405867` `61522e1` | UTF-8, path security, config fix, report completeness, Anthropic effort fix |
| 2026-05-30 | `afdc6d4` `8e7654f` | LangGraph deprecation suppression, drop empty-memory placeholder from prompts |
| 2026-05-30 | `0fda245` `bba1477` | Structured output for PM/RM/Trader; 5-tier rating; `rating.py` + `structured.py` utils; `SignalProcessor` no longer makes LLM calls |
| 2026-05-30 | `ebd2e12` | Memory migration: BM25 → `TradingMemoryLog`; `memory_seed.py` → `build_doctrine_context()`; deferred yfinance reflection lifecycle |
| 2026-06-21 | `a66aa8f` `dab0768` `0c1231a` `e4be7cc` `1ff3f07` `6560883` `9fd54f8` `7df18fc` | Data quality: yfinance pin, end-date fix, stale OHLCV guard, AV timeout, vendor chain config, VendorError hierarchy, `symbol_utils.py` (pulled as dependency) |
| 2026-06-21 | `d7b40a2` `47cbb32` `7c8fe2f` `4e7821d` | Identity: wrong-company hallucination fix, verified market snapshot, symbol normalization on reflection path, market ToolNode wired |
| 2026-06-21 | `81f7071` *(ours)* | Post-merge: added `benchmark` param to `_fetch_returns` for test compatibility; upstream `c15200d` is the new high-water mark |
