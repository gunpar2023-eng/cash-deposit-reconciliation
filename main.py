"""
Command line entry point.

    python main.py --data sample_data --out reconciliation.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

import workbook
from reconcile import MatchIndex, global_check, load_bank_files, reconcile


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile store cash deposits against bank statements.")
    ap.add_argument("--data", type=Path, default=Path("sample_data"),
                    help="folder holding cash_ledger.csv, store_master.csv and the bank files")
    ap.add_argument("--out", type=Path, default=Path("reconciliation.xlsx"))
    args = ap.parse_args(argv)

    data = args.data
    if not data.exists():
        print(f"No such folder: {data}. Run generate_sample_data.py first.", file=sys.stderr)
        return 1

    ledger = pd.read_csv(data / "cash_ledger.csv", dtype={"store_code": str})
    master = pd.read_csv(data / "store_master.csv", dtype={"store_code": str})
    staff_path = data / "staff_roster.csv"
    staff = pd.read_csv(staff_path, dtype={"store_code": str}) if staff_path.exists() else None

    known = {"cash_ledger.csv", "store_master.csv", "staff_roster.csv"}
    bank_files = sorted(p for p in data.glob("*.csv") if p.name not in known)
    if not bank_files:
        print(f"No bank files found in {data}.", file=sys.stderr)
        return 1

    index = MatchIndex.build(master, staff)
    deposits = load_bank_files(bank_files, index)
    comparison = reconcile(ledger, deposits)
    checks = global_check(comparison, deposits)

    workbook.write(args.out, comparison, deposits, checks)

    exceptions = (comparison.difference.abs() >= 0.01).sum()
    unmatched = int((deposits.store_code == "").sum())

    print(f"Bank files read        : {len(bank_files)}")
    print(f"Deposit rows in scope  : {len(deposits)}")
    print(f"Duplicates excluded    : {checks['duplicates_excluded']}")
    print(f"Unattributed deposits  : {unmatched}  ({checks['unattributed']:,.2f})")
    print(f"Stores compared        : {len(comparison)}")
    print(f"Stores with a difference: {exceptions}")
    print(f"Attribution ties out   : {'yes' if checks['balanced'] else 'NO - investigate'}")
    print(f"\nWritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
