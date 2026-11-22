"""
Store cash deposit reconciliation.

Compares what each store says it banked (the cash ledger) against what the
bank statements actually show, and produces a reconciliation workbook.

Design rules, learned the hard way:

1. The spreadsheet is an output, not a calculation engine. All matching,
   grouping and summing happens in Python. Excel receives plain values and
   simple reference formulas. Live array formulas in a delivered workbook
   break the moment someone sorts a column.

2. Store codes are text, not numbers. "00003" is not 3. Write the number
   format before writing the value or the leading zero is gone.

3. A name beats a malformed code. If a bank row carries code "1600" but the
   name on it is a store that is actually code "01600", trust the name.
   Matching on the broken code produces a silent, confident, wrong answer.

4. Duplicates within one source are not summed; the same movement re-exported
   twice is still one movement. Deposits at two different banks on the same
   day ARE summed, because they are two real movements.

5. An unmatched record is never attributed by guesswork. It stays visible,
   with its amount, flagged for a human. An amount coincidentally equalling
   some store's variance is not evidence.

6. "The totals agree" is not proof the reconciliation is right. The global
   check below is a necessary condition, not a sufficient one.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

UNMATCHED = "REVIEW"

# Rows whose description matches these are money leaving the account, or
# non-cash settlement. They are not store deposits.
EXCLUDE_PATTERNS = [
    r"\bfee\b", r"\bcharge\b", r"\btransfer\b", r"\bsettlement\b",
    r"\bfx\b", r"\bexchange\b", r"\bcard\b", r"\breversal\b",
]


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

def normalise_code(raw, width: int = 5) -> str:
    """Digits only, zero-padded to a fixed width. Empty if there are no digits."""
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits.zfill(width) if digits else ""


def normalise_name(raw) -> str:
    """Casefold and strip punctuation so 'Harbour Point' == 'HARBOUR  POINT.'"""
    s = str(raw or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_amount(raw) -> float:
    """
    Bank exports hand you '1.234,56' or '1234,56' or '1234.56' depending on the
    locale of whoever pressed export. Decide by which separator comes last.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    s = str(raw or "").strip()
    if not s:
        return 0.0
    s = re.sub(r"[^\d,.\-]", "", s)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def is_outgoing(description: str) -> bool:
    d = str(description or "").lower()
    return any(re.search(p, d) for p in EXCLUDE_PATTERNS)


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------

@dataclass
class MatchIndex:
    """Lookup tables built once, queried per bank row."""
    by_code: dict = field(default_factory=dict)
    by_name: dict = field(default_factory=dict)
    by_person: dict = field(default_factory=dict)

    @classmethod
    def build(cls, master: pd.DataFrame, staff: pd.DataFrame | None) -> "MatchIndex":
        idx = cls()
        for _, r in master.iterrows():
            code = normalise_code(r.store_code)
            idx.by_code[code] = code
            idx.by_name[normalise_name(r.store_name)] = code
        if staff is not None:
            for _, r in staff.iterrows():
                idx.by_person[normalise_name(r.full_name)] = normalise_code(r.store_code)
        return idx

    def lookup(self, reference, description) -> tuple[str, str]:
        """
        Return (store_code, how). Order matters: name before code, because a
        truncated code is more common than a wrong name.
        """
        desc = normalise_name(description)

        for name, code in self.by_name.items():
            if name and name in desc:
                return code, "name"

        code = normalise_code(reference)
        if code and code in self.by_code:
            return code, "code"

        for person, code in self.by_person.items():
            if person and person in desc:
                return code, "staff"

        # Last resort: a bare number anywhere in the description.
        for token in re.findall(r"\d+", desc):
            padded = normalise_code(token)
            if padded in self.by_code:
                return padded, "code-in-text"

        return "", UNMATCHED


# --------------------------------------------------------------------------
# core
# --------------------------------------------------------------------------

def load_bank_files(paths: list[Path], index: MatchIndex) -> pd.DataFrame:
    """
    Read every bank file into one frame of deposits.

    `source_bank` is taken from the filename up to the first underscore, so
    NORTHBANK_part1.csv and NORTHBANK_part2.csv are understood as two exports
    of the same bank -- which is what makes duplicate detection possible.
    """
    rows = []
    for path in paths:
        df = pd.read_csv(path)
        bank = path.stem.split("_")[0].upper()
        for line_no, r in enumerate(df.itertuples(index=False), start=2):
            desc = getattr(r, "description", "")
            amount = parse_amount(getattr(r, "amount", 0))

            if amount <= 0 or is_outgoing(desc):
                continue

            code, how = index.lookup(getattr(r, "reference", ""), desc)
            rows.append({
                "store_code": code,
                "description": str(desc).strip(),
                "amount": round(amount, 2),
                "value_date": getattr(r, "value_date", ""),
                "source_bank": bank,
                "source_file": path.name,
                "source_line": line_no,
                "matched_by": how,
            })

    if not rows:
        return pd.DataFrame(columns=[
            "store_code", "description", "amount", "value_date",
            "source_bank", "source_file", "source_line", "matched_by", "note",
        ])

    out = pd.DataFrame(rows)
    out = flag_duplicates(out)
    out["note"] = out.apply(_note, axis=1)
    return out


def flag_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Within one bank, an identical (store, amount, date) seen more than once is
    the same movement exported twice. Keep the first, mark the rest.
    Across different banks, identical rows are two real deposits -- left alone.
    """
    df = df.copy()
    key = ["source_bank", "store_code", "amount", "value_date"]
    df["is_duplicate"] = df.duplicated(subset=key, keep="first") & (df.store_code != "")
    return df


def _note(row) -> str:
    if row.is_duplicate:
        return f"Duplicate of an earlier {row.source_bank} row - excluded from totals"
    if row.matched_by == UNMATCHED:
        return "Could not be attributed to a store - needs review"
    if row.matched_by in ("staff", "code-in-text"):
        return f"Attributed via {row.matched_by}, not a clean code - worth a check"
    return ""


def reconcile(ledger: pd.DataFrame, deposits: pd.DataFrame) -> pd.DataFrame:
    """One row per store. Every store, including the ones that agree."""
    counted = deposits[(~deposits.is_duplicate) & (deposits.store_code != "")]
    per_store = counted.groupby("store_code")["amount"].sum()

    rows = []
    for _, s in ledger.iterrows():
        code = normalise_code(s.store_code)
        # The ledger holds deposits as negative (money out of the till).
        ledger_amount = round(abs(float(s.deposited_to_bank)), 2)
        bank_amount = round(float(per_store.get(code, 0.0)), 2)
        rows.append({
            "store_code": code,
            "store_name": s.store_name,
            "per_ledger": ledger_amount,
            "per_bank": bank_amount,
            "difference": round(bank_amount - ledger_amount, 2),
            "date": s.date,
        })
    return pd.DataFrame(rows)


def global_check(comparison: pd.DataFrame, deposits: pd.DataFrame) -> dict:
    """
    Necessary condition: everything the bank shows is either tied to a store
    or sitting in the unmatched bucket. Nothing evaporates.
    """
    counted = deposits[~deposits.is_duplicate]
    attributed = counted[counted.store_code != ""].amount.sum()
    unattributed = counted[counted.store_code == ""].amount.sum()
    return {
        "bank_total": round(counted.amount.sum(), 2),
        "attributed": round(attributed, 2),
        "unattributed": round(unattributed, 2),
        "ledger_total": round(comparison.per_ledger.sum(), 2),
        "net_difference": round(comparison.difference.sum(), 2),
        "duplicates_excluded": int(deposits.is_duplicate.sum()),
        "balanced": bool(abs(round(attributed - comparison.per_bank.sum(), 2)) < 0.01),
    }
