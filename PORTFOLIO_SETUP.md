# Portfolio Analysis Setup Guide

This guide covers the personal portfolio analysis layer (`portfolio_lib` + `analyze_portfolio.py`) built on top of TradingAgents. It runs a full multi-agent LLM pipeline against your holdings and watchlist and writes markdown reports to an `Analysis/` folder.

---

## Prerequisites

- Python 3.11 or higher
- An [Anthropic API key](https://console.anthropic.com) (Claude powers the analysis)
- Optional: an [Alpha Vantage API key](https://www.alphavantage.co/support/#api-key) (free tier, 25 calls/day — only needed for the `--av` flag)

---

## 1. Clone and install

```bash
git clone https://github.com/SpookyFox9/TradingAgents.git
cd TradingAgents
```

Create and activate a virtual environment:

```bash
# Mac / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> **Windows only:** if activation is blocked, run this once as Administrator:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Install dependencies:

```bash
pip install -e .
```

---

## 2. Set up your API key

```bash
cp .env.example .env      # Mac / Linux
copy .env.example .env    # Windows
```

Open `.env` and fill in your Anthropic key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

The `.env` file is gitignored and will never be committed.

---

## 3. Set up your portfolio

```bash
cp portfolio.json.example portfolio.json      # Mac / Linux
copy portfolio.json.example portfolio.json    # Windows
```

Open `portfolio.json` and replace the placeholder values with your actual holdings. See the field reference below.

### portfolio.json field reference

```jsonc
{
  "owner": "Your Name",           // optional label, not used by the analysis
  "last_updated": "2024-01-01",   // optional, informational only
  "cash_balance": 1500.00,        // uninvested cash in your account

  "holdings": [
    {
      "ticker": "AAPL",           // stock ticker symbol (uppercase)
      "entry": 150.00,            // your average cost basis per share
      "shares": 10.0,             // number of shares held
      "acquired_date": "2024-01-15"  // optional, ISO format YYYY-MM-DD
    }
    // add one object per holding
  ],

  "open_orders": [],              // optional, informational only — not analyzed

  "watch_list": ["NVDA", "META"], // tickers to analyze with --watchlist

  "targets": {
    "NVDA": 100.00                // optional entry price target per watchlist ticker
  },

  "strategy": "Describe your investing style here"
                                  // injected into agent context as a one-liner
}
```

**Notes:**
- Warrants or positions with no meaningful cost basis: set `"entry": 0.0` — the pipeline skips them automatically.
- `holdings` are analyzed by default. `watch_list` tickers are analyzed only when you pass `--watchlist`.
- The file is gitignored. It will not be committed if you keep it inside this directory.

---

## 4. Run the analysis

```bash
# Analyze all holdings (default)
python analyze_portfolio.py

# Analyze specific tickers only
python analyze_portfolio.py --tickers AAPL MSFT

# Analyze your watchlist
python analyze_portfolio.py --watchlist

# Deep mode — 2x debate rounds (~30% more tokens, better for high-stakes decisions)
python analyze_portfolio.py --deep

# Discover N new candidates via a two-pass GARP screen
python analyze_portfolio.py --discover 3

# Validate config without making any LLM calls
python analyze_portfolio.py --dry-run
```

If your `portfolio.json` lives somewhere other than inside the `TradingAgents/` directory:

```bash
python analyze_portfolio.py --portfolio /path/to/your/portfolio.json
```

### Analyst presets

| Flag | Analysts included | Best for |
|------|------------------|----------|
| *(default)* `--analyst-set quality` | market, news, fundamentals | Long-term investing |
| `--analyst-set full` | market, social, news, fundamentals | Complete picture |
| `--analyst-set fast` | market, fundamentals | Quick check |

---

## 5. Output files

Reports are written to `Analysis/` (created automatically next to your `portfolio.json`):

| File | Contents |
|------|----------|
| `YYYY-MM-DD_HHmm_TICKER.md` | Full per-ticker report — decision, all agent reasoning, trailing stop |
| `YYYY-MM-DD_HHmm_SUMMARY.md` | Digest — decisions table, action items, signal track record |
| `signal_log.jsonl` | Append-only record of every signal for automatic grading |
| `cost_log.jsonl` | Token usage and USD cost per run (per-model breakdown) |

---

## 6. Optional: customize the analyst voice

Create an `Investor_Persona.md` file inside the `TradingAgents/` directory. The pipeline injects its full content into every agent prompt, shaping how they reason and respond.

You can describe your investment style, risk tolerance, what you care about, and how you want answers formatted. If the file doesn't exist, the pipeline uses a built-in GARP-oriented default.

Example structure:

```markdown
# Investor Identity
You are a long-term value investor focused on dividend growth and capital preservation...

# Core Doctrine
- Prefer companies with 10+ year dividend growth streaks
- ROIC above 12%, FCF yield above 4%
- Avoid companies with debt/equity > 1.5

# Response Format
Lead with conviction level, then a brief thesis, then risks.
```

The file is gitignored and will not be committed.

---

## 7. Cost estimates

Each ticker runs a full multi-agent pipeline (market + news + fundamentals analysts → bull/bear researchers → risk → trader). Using Claude Sonnet + Haiku:

| Mode | Approx. cost per ticker |
|------|------------------------|
| Default (quality, 1 round) | ~$0.75 |
| Deep (quality, 2 rounds) | ~$1.00 |
| Full analysts (1 round) | ~$0.90 |

Actual costs are recorded in `Analysis/cost_log.jsonl` after every run.

---

## 8. Troubleshooting

**`ANTHROPIC_API_KEY is not set`**
Check that `.env` exists inside the `TradingAgents/` directory and contains your key. The file must be named `.env` exactly (not `env` or `.env.txt`).

**`ModuleNotFoundError: tradingagents`**
Run `pip install -e .` with the virtual environment active. Confirm the venv is active — you should see `(.venv)` in your prompt.

**`FileNotFoundError: portfolio.json`**
Either copy `portfolio.json.example` to `portfolio.json` inside `TradingAgents/`, or pass the path explicitly: `python analyze_portfolio.py --portfolio /path/to/portfolio.json`.

**`Activate.ps1 cannot be loaded` (Windows)**
Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` in PowerShell as Administrator, then try activating again.
