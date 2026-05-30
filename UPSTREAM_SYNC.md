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
| `tradingagents/graph/trading_graph.py` | We added `safe_ticker_component`; upstream memory refactor conflicts here |
| `tradingagents/graph/propagation.py` | Upstream threads recursion limit through here |
| `tradingagents/agents/utils/memory.py` | Upstream replaced BM25 with decision log (Phase 3 migration pending) |
| `tests/conftest.py` | We restored this after upstream deleted it |

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
| Ollama, OpenRouter, DeepSeek, Qwen, GLM, MiniMax, Azure, xAI, grok | Providers we don't use |
| Docker | Not our deployment model |
| CLI dropdown, `.env` persistence | We use `run_analysis.ps1` + Windows User env vars |
| i18n, localize, multi-language | English only |
| crypto, asset_type | Equities only |
| `api.tauric.ai` announcements | Tauric SaaS feature |

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

### Phase 1 — Easy wins ✅ READY
```
afdc6d4  chore: suppress upstream langgraph allowed_objects deprecation noise
8e7654f  fix: drop past-memory directive and placeholder from agent prompts when memory is empty
```
Cherry-pick both. ~30 min.

### Phase 2 — Structured Output 🔶 REVIEW FIRST
```
0fda245  feat: structured-output Portfolio Manager + 5-tier rating consistency
bba1477  feat: structured-output Trader and Research Manager
```
Changes how `final_trade_decision` is formatted. Before taking:
1. Read diffs on both commits
2. Verify `portfolio_lib/analyzer.py` signal extraction still works (`result.decision`)
3. Run `test_analyzer_integration.py` after applying

### Phase 3 — Memory Migration 🔴 DEDICATED SPRINT
```
ebd2e12  feat: replace per-agent BM25 memory with persistent decision log
```
Removes `rank-bm25`. Adds `~/.tradingagents/memory/trading_memory.md` — auto-appended
after each run, resolved on next same-ticker run with actual returns + LLM reflection.

**Decision:** Full migration — rewrite `portfolio_lib/memory_seed.py` to inject doctrine
into the new decision log format on first run, rather than seeding BM25 each time.  
**Conflicts:** `tradingagents/agents/utils/memory.py`, `trading_graph.py`, `reflection.py`, `setup.py`.

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
