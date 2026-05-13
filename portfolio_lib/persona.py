"""Investor persona — single source of truth for the default investor strategy voice."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InvestorPersona:
    name: str
    style: str
    time_horizon: str
    red_flags: list[str]
    response_structure: str


_PERSONA_TEXT_PATH = Path(__file__).resolve().parent.parent.parent / "Investor_Persona.md"


def _load_raw() -> str:
    try:
        return _PERSONA_TEXT_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


DEFAULT_PERSONA = InvestorPersona(
    name="Senior Investment Strategist",
    style="GARP (Growth at a Reasonable Price) with AI infrastructure concentration (Barbell Architecture)",
    time_horizon="Long-term (12–24+ months); full-engine configuration since 2026-05-13 (Risk-On regime, shields fully rotated out); re-deploy shields on sustained Risk-Off signal",
    red_flags=[
        "Rising debt without revenue acceleration",
        "Falling FCF margin (free cash flow ÷ revenue) — signals moat erosion",
        "Declining ROIC (return on invested capital) — efficiency deteriorating",
        "Loss of moat evidence: customer churn, pricing power loss, margin compression",
        "AI stack dependency risk: grid → cooling → chip → software supply chain disruption",
    ],
    response_structure=(
        "Confidence Level: [X%] | Tactical Brief | Risk & Vulnerability Scan | Next Step"
    ),
)


def render(persona: InvestorPersona | None = None, include_full_text: bool = True) -> str:
    """Return the persona block to inject into agent context."""
    p = persona or DEFAULT_PERSONA
    raw = _load_raw() if include_full_text else ""

    sections: list[str] = [
        "## INVESTOR PROFILE",
        f"**Strategist:** {p.name}",
        f"**Strategy:** {p.style}",
        f"**Time Horizon:** {p.time_horizon}",
        "**Quality Red Flags:** " + " | ".join(p.red_flags),
        f"**Expected Response Format:** {p.response_structure}",
    ]
    if raw:
        sections += ["", "## FULL INVESTOR DOCTRINE", raw]

    return "\n".join(sections)
