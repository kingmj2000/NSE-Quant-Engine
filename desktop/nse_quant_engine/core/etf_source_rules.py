"""Unit and identity rules for ETF metadata taken from AMFI disclosures.

One home for facts about the source, so the AUM fetcher, the TER/tracking
fetcher and the metadata enricher cannot drift apart again. Each rule below was
broken in a real import file and is pinned by a regression test.

1. AMFI publishes TER, tracking error and tracking difference as PERCENT values
   ("0.04" means 0.04%). The old "anything <= 0.05 is already a decimal" guess
   stored 26 Nifty 50 / Sensex ETFs at 4.00% instead of 0.04%.
2. Until 31 Mar 2026 an ETF's total expense ratio was capped at 1.00% of daily
   net assets (SEBI MF Regulations 1996, Reg. 52(6)). Since 1 Apr 2026 (SEBI MF
   Regulations 2026) the cap is on the base expense ratio, 0.90% for ETFs and
   index funds, and statutory levies are charged at actuals on top, so a total
   above 1.00% can be a real levy spike as well as a unit error or another
   scheme's number. Totals above 1.00% are still left blank (the bound is
   unchanged); the TER diagnostics show the disclosed figure and its split.
3. AMFI scheme-wise average AUM is published in Rs LAKH. Reading it as crore
   (or dividing anything above 10 lakh by 1e7) put NIFTYBEES at 0.57 cr instead
   of about 57,000 cr.
4. Labels such as "Other Scheme - Other  ETFs" are AMFI scheme categories, not
   benchmark indices. A row whose category is not an ETF category (index fund,
   fund of funds, active equity, liquid fund, ...) describes a different scheme
   from the ETF, so none of its numbers belong to the ETF.
5. Since 2026 AMFI also files schemes under the new SEBI category names
   ("Exchange Traded Funds (ETFs) - Equity ETF", "Index Funds - Equity Funds",
   "Income/Debt Oriented Schemes - Liquid Fund", ...). The old pattern knew
   none of them: 174 of the 206 "benchmarks" stored in the 2026-11-05 run were
   such labels, and index funds / FoFs under the new names were not rejected.
6. A number disclosed for one fund house's scheme never belongs to another
   fund house's ETF. Name-only fuzzy matching with the AMC words dropped gave
   184 of 319 matched ETFs another AMC's row in the 2026-11-05 run, 176 of the
   287 TERs included (SBI Nifty Bank ETF took Baroda BNP Paribas Nifty Bank
   ETF's). amc_of() names the fund house so the matcher can refuse those rows.
"""
from __future__ import annotations

import math
import re
from typing import Any

# Bumped whenever the fetchers change how they read AMFI files. Import files
# written by an older parser are re-fetched.
#   v2: AAUM read in Rs lakh; TER read as percent; category labels split out.
#   v3: SEBI 2026 category labels; TER/TE/TD matched per stream, same AMC only.
AMFI_PARSER_VERSION = 3
# Oldest parser whose numbers are still trusted, per disclosure stream. AUM
# parsing did not change in v3, so a v2 AUM file stays usable; TER/tracking
# files older than v3 carry cross-AMC values and are never scored.
AMFI_AUM_MIN_PARSER_VERSION = 2
AMFI_TER_MIN_PARSER_VERSION = 3

ETF_TER_CAP_DECIMAL = 0.01          # total TER bound for ETFs / index funds (rule 2)
AMFI_AAUM_UNIT = "lakh"             # AMFI scheme-wise AAUM: Rs lakh

_CATEGORY_GROUP = (
    r"other|equity|debt|hybrid|solution\s+oriented|growth|income|"
    r"income\s*/\s*debt\s+oriented|close\s+ended|interval|"
    r"fund\s+of\s+funds?(?:\s+schemes?)?(?:\s*\((?:domestic|overseas)\))?|"
    r"overseas\s+fund\s+of\s+funds?|"
    r"exchange\s+traded\s+funds?(?:\s*\(etfs?\))?|"
    r"index\s+funds?|life\s+cycle\s+funds?|children\W?s\W?\s+funds?"
)
_CATEGORY_LABEL = re.compile(
    r"^\s*(?:" + _CATEGORY_GROUP + r")(?:\s+schemes?)?\s*(?:\*+\s*)?-\s*\S",
    re.IGNORECASE,
)
# Category values AMFI also files without a "<group> - " prefix.
_BARE_CATEGORY_LABELS = {
    "elss", "income", "growth", "liquid", "gilt", "balanced", "money market",
    "index fund", "index funds", "fof domestic", "fof overseas", "other etfs",
}
_FOF_TEXT = re.compile(r"\bfof\b|\bfund\s+of\s+funds?\b", re.IGNORECASE)
_INDEX_FUND_NAME = re.compile(r"\bindex\s+(?:fund|plan)s?\b", re.IGNORECASE)
_ETF_NAME = re.compile(r"\b(?:etfs?|bees|exchange\s+traded)\b", re.IGNORECASE)


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
    if " ".join(text.lower().split()) in _BARE_CATEGORY_LABELS:
        return True
    return bool(_CATEGORY_LABEL.match(text))


def is_etf_category(value: Any) -> bool:
    """ETF categories, old and new; a fund of funds that buys ETFs is not one."""
    text = str(value or "").lower()
    if _FOF_TEXT.search(text):
        return False
    return "etf" in text or "exchange traded" in text


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


def scheme_kind(name: Any, category: Any = "") -> str:
    """"etf", "non_etf" or "" (unknown) for an AMFI scheme row.

    A recognised category decides. Without one (AMFI tracking-error and
    tracking-difference rows carry no category) the name does: "... Index
    Fund", "... FoF" and "... Fund of Fund" are not ETFs even when the name
    also says ETF ("Nifty 50 ETF FOF").
    """
    if is_amfi_category_label(category):
        return "etf" if is_etf_category(category) else "non_etf"
    text = "" if name is None else str(name)
    if _FOF_TEXT.search(text) or _INDEX_FUND_NAME.search(text):
        return "non_etf"
    if _ETF_NAME.search(text):
        return "etf"
    return ""


# Fund-house names as they appear in AMFI scheme names (cleaned: lower case,
# "&" -> "and", other punctuation -> space). Matched as whole words; the
# earliest match in the text wins, because AMFI names start with the AMC.
_AMC_PHRASES = (
    ("360one", ("360 one",)),
    ("angelone", ("angel one",)),
    ("birla", ("aditya birla", "birla sun life", "absl")),
    ("axis", ("axis",)),
    ("bajaj", ("bajaj finserv", "bajaj")),
    ("bandhan", ("bandhan", "idfc")),
    ("baroda", ("baroda bnp paribas", "baroda bnp", "baroda", "bnp paribas")),
    ("canara", ("canara robeco", "canara")),
    ("choice", ("choice",)),
    ("dsp", ("dsp",)),
    ("edelweiss", ("edelweiss",)),
    ("franklin", ("franklin",)),
    ("groww", ("groww",)),
    ("hdfc", ("hdfc",)),
    ("hsbc", ("hsbc",)),
    ("icici", ("icici",)),
    ("invesco", ("invesco",)),
    ("jio", ("jioblackrock", "jio blackrock")),
    ("kotak", ("kotak",)),
    ("lic", ("lic mf", "lic")),
    ("mahindra", ("mahindra manulife",)),
    ("mirae", ("mirae",)),
    ("motilal", ("motilal",)),
    ("navi", ("navi",)),
    ("nippon", ("nippon",)),
    ("pgim", ("pgim",)),
    ("quant", ("quant",)),
    ("quantum", ("quantum",)),
    ("samco", ("samco",)),
    ("sbi", ("sbi",)),
    ("shriram", ("shriram",)),
    ("sundaram", ("sundaram",)),
    ("tata", ("tata",)),
    ("union", ("union",)),
    ("uti", ("uti",)),
    ("wealth", ("the wealth company", "wealth company")),
    ("whiteoak", ("whiteoak", "white oak")),
    ("zerodha", ("zerodha",)),
)
_AMC_PATTERNS = [
    (amc, re.compile(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])"))
    for amc, phrases in _AMC_PHRASES for p in phrases
]
# NSE issuer codes run the AMC into one token ("NIPINDETFNIFTYBEES",
# "BIRLASLAMC", "RELIGARAMC"). Prefixes of the compacted text, longest first.
_AMC_CODE_PREFIXES = sorted([
    ("nipind", "nippon"), ("nippon", "nippon"), ("namindia", "nippon"), ("relcap", "nippon"),
    ("religar", "invesco"), ("invesco", "invesco"),
    ("icici", "icici"), ("hdfc", "hdfc"), ("kotak", "kotak"), ("motilal", "motilal"),
    ("mirae", "mirae"), ("birla", "birla"), ("absl", "birla"), ("aditya", "birla"),
    ("zerodha", "zerodha"), ("groww", "groww"), ("bandhan", "bandhan"), ("edel", "edelweiss"),
    ("baroda", "baroda"), ("bajaj", "bajaj"), ("bfam", "bajaj"), ("quantum", "quantum"),
    ("jioblackrock", "jio"), ("aoneamc", "angelone"), ("angelone", "angelone"),
    ("360one", "360one"), ("licmf", "lic"), ("licnamc", "lic"), ("tataam", "tata"),
    ("dspamc", "dsp"), ("utiamc", "uti"), ("sbiamc", "sbi"), ("axisamc", "axis"),
    ("hsbcamc", "hsbc"), ("unionamc", "union"), ("choiceamc", "choice"),
    ("shriram", "shriram"), ("wealth", "wealth"),
], key=lambda kv: -len(kv[0]))
# AMFI NSDL scheme-code prefixes (TER sheets), as filed in November 2026.
_NSDL_AMC_PREFIX = {
    "360O": "360one", "ABSL": "birla", "AOMF": "angelone", "AXIS": "axis", "BBNP": "baroda",
    "BNPP": "baroda", "BFAM": "bajaj", "BNDN": "bandhan", "IDFC": "bandhan", "CANA": "canara",
    "CHMF": "choice", "DSPM": "dsp", "EDEL": "edelweiss", "FTMF": "franklin", "GROW": "groww",
    "INDB": "groww", "HDFC": "hdfc", "HSBC": "hsbc", "LTMF": "hsbc", "ICIC": "icici",
    "INVM": "invesco", "JBMF": "jio", "KOTM": "kotak", "LICM": "lic", "MAHM": "mahindra",
    "MIRA": "mirae", "MOTO": "motilal", "NAVI": "navi", "NIPP": "nippon", "PGIM": "pgim",
    "QNTM": "quant", "QTMM": "quantum", "SAMC": "samco", "SBIM": "sbi", "SHRI": "shriram",
    "SUND": "sundaram", "TATA": "tata", "TWCF": "wealth", "UNIN": "union", "UTIM": "uti",
    "WHIT": "whiteoak", "ZERO": "zerodha",
}
# AMFI mfId values seen in the tracking-error / tracking-difference feeds.
_AMFI_MF_ID_AMC = {
    "3": "birla", "4": "baroda", "6": "dsp", "9": "hdfc", "17": "kotak", "20": "icici",
    "21": "nippon", "22": "sbi", "28": "uti", "41": "quantum", "42": "invesco",
    "47": "edelweiss", "48": "bandhan", "53": "axis", "54": "navi", "55": "motilal",
    "63": "groww", "75": "bajaj", "80": "angelone", "82": "jio", "84": "choice",
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).lower().replace("&", " and ")
    if text.strip() in {"nan", "none"}:
        return ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def amc_of(value: Any) -> str:
    """Canonical fund house named in a scheme name or NSE issuer code, or ""."""
    text = _clean(value)
    if not text:
        return ""
    best = None
    for amc, pattern in _AMC_PATTERNS:
        m = pattern.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), amc)
    if best:
        return best[1]
    compact = text.replace(" ", "")
    for prefix, amc in _AMC_CODE_PREFIXES:
        if compact.startswith(prefix):
            return amc
    return ""


def amc_of_scheme_code(code: Any) -> str:
    """Fund house from an AMFI NSDL scheme code ("ZERO/O/O/EET/24/04/0005")."""
    text = "" if code is None else str(code).strip().upper()
    return _NSDL_AMC_PREFIX.get(text.split("/")[0].strip(), "") if "/" in text else ""


def amc_of_mf_id(mf_id: Any) -> str:
    """Fund house from the mfId column of AMFI's tracking feeds."""
    text = "" if mf_id is None else str(mf_id).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return _AMFI_MF_ID_AMC.get(text, "")
