"""Deterministic relevance + event-category classification.

Guardrails:
- Symbol matching uses token boundaries — never substring.
- Multi-token aliases require the full phrase or at least two distinctive tokens.
- Generic industry tokens (bank, power, steel, india, energy, finance, motors,
  industries, company, corporation, holdings, ltd, limited) are never
  sufficient alone.
- Official filing relevance comes from the exchange symbol mapping, not text
  guessing.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable
import pandas as pd

LEGAL_SUFFIXES = re.compile(
    r"\b(ltd|limited|inc|corp|corporation|company|co|plc|pvt|private|industries|holdings|group|the)\b\.?",
    re.IGNORECASE,
)
NON_WORD = re.compile(r"[^A-Za-z0-9]+")
GENERIC_TOKENS = {
    "bank", "power", "steel", "india", "indian", "energy", "finance",
    "financial", "motors", "motor", "industries", "industry", "company",
    "corporation", "holdings", "holding", "ltd", "limited", "group",
    "the", "and", "of", "for", "auto", "cement", "oil", "gas", "life",
    "health", "care", "tech", "technologies", "solutions", "services",
}

EVENT_RULES: list[tuple[str, re.Pattern]] = [
    ("Results/Earnings", re.compile(r"\b(q[1-4]|quarter(ly)?|earnings|results|profit|revenue|ebitda|topline|bottomline|net profit)\b", re.I)),
    ("Corporate Action", re.compile(r"\b(dividend|bonus|split|record date|buyback|buy-back|rights issue|ex-dividend)\b", re.I)),
    ("Fundraising",     re.compile(r"\b(qip|preferential|fund ?rais(e|ing)|ncd|debentures?|bond issue|ipo|fpo|placement)\b", re.I)),
    ("Credit Rating",   re.compile(r"\b(credit rating|rating(s)? (upgrade|downgrade|revision|affirm|assign)|crisil|icra|care ratings|india ratings|moody's|s&p|fitch)\b", re.I)),
    ("Regulatory/Legal",re.compile(r"\b(sebi|rbi|cci|nclt|court|tribunal|lawsuit|litigation|probe|investigation|penalty|show cause|notice)\b", re.I)),
    ("M&A",             re.compile(r"\b(merger|acquisition|acquires?|acquired|takeover|demerger|scheme of arrangement|amalgamation|stake sale|divest)\b", re.I)),
    ("Management",      re.compile(r"\b(ceo|cfo|md|chairman|managing director|resign|resignation|appoint(ed|ment)?|steps? down)\b", re.I)),
    ("Order/Contract",  re.compile(r"\b(order (win|book|received)|contract (award|win)|bags? order|loi|letter of intent|awarded)\b", re.I)),
    ("Product/Operational", re.compile(r"\b(launch|unveils|commission(ed|ing)?|plant|capacity expansion|new facility|production)\b", re.I)),
    ("Analyst/Broker Opinion", re.compile(r"\b(target price|price target|upgrade|downgrade|initiate coverage|analyst|brokerage|jefferies|morgan stanley|citi|nomura|kotak|motilal|hsbc|goldman)\b", re.I)),
]


def _tokenize(text: str) -> list[str]:
    return [t for t in NON_WORD.sub(" ", (text or "")).lower().split() if t]


def _distinctive_tokens(text: str) -> list[str]:
    return [t for t in _tokenize(text) if t not in GENERIC_TOKENS and len(t) >= 4]


def strip_legal_suffixes(name: str) -> str:
    s = LEGAL_SUFFIXES.sub(" ", name or "")
    return NON_WORD.sub(" ", s).strip()


def load_alias_overrides(path: Path) -> dict[str, list[str]]:
    path = Path(path)
    out: dict[str, list[str]] = {}
    if not path.exists():
        return out
    try:
        df = pd.read_csv(path, comment="#")
    except Exception:
        return out
    if "Symbol" not in df.columns or "Alias" not in df.columns:
        return out
    for _, r in df.iterrows():
        sym = str(r["Symbol"]).strip().upper()
        alias = str(r["Alias"]).strip()
        if sym and alias:
            out.setdefault(sym, []).append(alias)
    return out


def build_aliases(name: str, symbol: str, overrides: dict[str, list[str]] | None = None) -> list[str]:
    """Return list of approved matching phrases for the candidate.

    Every alias returned is one of:
      - the bare NSE symbol
      - the full company name (as given)
      - the company name with legal suffixes stripped, if it still has ≥ 2
        distinctive tokens
      - user-supplied override aliases
    Single generic tokens are never emitted as aliases.
    """
    aliases: list[str] = []
    sym = (symbol or "").strip().upper()
    if sym:
        aliases.append(sym)
    nm = (name or "").strip()
    if nm:
        aliases.append(nm)
    stripped = strip_legal_suffixes(nm)
    if stripped and stripped.lower() != nm.lower():
        if len(_distinctive_tokens(stripped)) >= 2 or len(stripped.split()) == 1 and stripped.lower() not in GENERIC_TOKENS:
            aliases.append(stripped)
    if overrides:
        for extra in overrides.get(sym, []):
            if extra and extra not in aliases:
                aliases.append(extra)
    # dedup preserving order
    seen = set(); out = []
    for a in aliases:
        k = a.lower()
        if k not in seen:
            seen.add(k); out.append(a)
    return out


def _symbol_token_hit(title: str, symbol: str) -> bool:
    if not symbol:
        return False
    return re.search(rf"\b{re.escape(symbol)}\b", title or "", re.IGNORECASE) is not None


def _alias_hit(title: str, alias: str) -> tuple[bool, str]:
    """Return (matched, reason). Multi-token aliases require full phrase or
    ≥2 distinctive tokens present as whole words."""
    if not alias or not title:
        return False, ""
    title_l = title.lower()
    alias_tokens = _tokenize(alias)
    if not alias_tokens:
        return False, ""
    # single-token alias
    if len(alias_tokens) == 1:
        tok = alias_tokens[0]
        if tok in GENERIC_TOKENS or len(tok) < 4:
            return False, ""
        if re.search(rf"\b{re.escape(tok)}\b", title_l):
            return True, "Exact alias match"
        return False, ""
    # multi-token: try full phrase first
    phrase_re = re.compile(r"\b" + r"\s+".join(re.escape(t) for t in alias_tokens) + r"\b", re.IGNORECASE)
    if phrase_re.search(title):
        return True, "Exact alias match"
    # fallback: ≥2 distinctive tokens
    distinctive = [t for t in alias_tokens if t not in GENERIC_TOKENS and len(t) >= 4]
    hits = sum(1 for t in distinctive if re.search(rf"\b{re.escape(t)}\b", title_l))
    if hits >= 2:
        return True, "Token alias match"
    return False, ""


def classify_relevance(
    title: str,
    symbol: str,
    aliases: list[str],
    is_official_filing: bool = False,
    filing_symbol: str | None = None,
) -> str | None:
    """Return Relevance_Reason string or None if the item should NOT be
    treated as candidate-specific."""
    if is_official_filing:
        if filing_symbol and filing_symbol.strip().upper() == (symbol or "").strip().upper():
            return "Official filing mapping"
        return None  # official filings must map by exchange identifier
    if _symbol_token_hit(title, symbol):
        return "Symbol match"
    for a in aliases:
        if a.strip().upper() == (symbol or "").strip().upper():
            continue  # handled above
        ok, reason = _alias_hit(title, a)
        if ok:
            return reason
    return None


def classify_event(title: str) -> str:
    t = title or ""
    for label, pattern in EVENT_RULES:
        if pattern.search(t):
            return label
    return "General Company News"
