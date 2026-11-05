"""Unit and identity rules for ETF metadata taken from AMFI disclosures.

One home for facts about the source, so the AUM fetcher, the TER/tracking
fetcher and the metadata enricher cannot drift apart again. Each rule below was
broken in the June 2026 import files and is pinned by a regression test.

1. AMFI publishes TER, tracking error and tracking difference as PERCENT values
   ("0.04" means 0.04%). The old "anything <= 0.05 is already a decimal" guess
   stored 26 Nifty 50 / Sensex ETFs at 4.00% instead of 0.04%.
2. An ETF's total expense ratio is capped at 1.00% of daily net assets
   (SEBI Mutual Funds Regulations, Reg. 52(6)). A higher figure is a unit
   error or another scheme's number, never a real ETF TER.
3. AMFI scheme-wise average AUM is published in Rs LAKH. Reading it as crore
   (or dividing anything above 10 lakh by 1e7) put NIFTYBEES at 0.57 cr instead
   of about 57,000 cr.
4. Labels such as "Other Scheme - Other  ETFs" are AMFI scheme categories, not
   benchmark indices. A row whose category is not an ETF category (index fund,
   fund of funds, active equity, liquid fund, ...) describes a different scheme
   from the ETF, so none of its numbers belong to the ETF.
"""
from __future__ import annotations

import math
import re
from typing import Any

# Bumped whenever the fetchers change how they read AMFI files. Import files
# written by an older parser are re-fetched, and their AUM is not trusted.
AMFI_PARSER_VERSION = 2

ETF_TER_CAP_DECIMAL = 0.01          # SEBI Reg. 52(6): ETFs / index funds <= 1.00%
AMFI_AAUM_UNIT = "lakh"             # AMFI scheme-wise AAUM: Rs lakh

_CATEGORY_LABEL = re.compile(
    r"^\s*(other|equity|debt|hybrid|solution\s+oriented|growth|income|"
    r"close\s+ended|interval|fund\s+of\s+funds?)\s+schemes?\s*-\s*\S",
    re.IGNORECASE,
)


def _num(value: Any) -> float:
    if value is None:
        return math.nan
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "-", "na", "n/a", "nil"}:
        return math.nan
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else math.nan


def amfi_percent_to_decimal(value: Any) -> float:
    """AMFI percent figure -> decimal. "0.04" and "0.04%" both mean 0.0004."""
    x = _num(value)
    return x / 100.0 if not math.isnan(x) else math.nan


def amfi_lakh_to_crore(value: Any) -> float:
    x = _num(value)
    return round(x / 100.0, 2) if not math.isnan(x) else math.nan


def is_amfi_category_label(value: Any) -> bool:
    """True for AMFI scheme-category strings, which are not benchmark names."""
    if value is None:
        return False
    text = str(value)
    if not text.strip() or text.strip().lower() in {"nan", "none"}:
        return False
    return bool(_CATEGORY_LABEL.match(text))


def is_etf_category(value: Any) -> bool:
    return "etf" in str(value or "").lower()


def is_non_etf_category(value: Any) -> bool:
    """A recognised AMFI category that is not an ETF category.

    Blank or unrecognised text returns False: only positive evidence of a
    different scheme type rejects a row.
    """
    return is_amfi_category_label(value) and not is_etf_category(value)


def benchmark_or_blank(value: Any) -> str:
    """The benchmark text, or "" when the value is really a category label."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"} or is_amfi_category_label(text):
        return ""
    return text
