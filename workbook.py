"""
Write the reconciliation workbook.

The sheets are built as real Excel tables with frozen headers. Values are
written by Python; the only formulas are single-cell references, so nothing
recalculates into a different answer on someone else's machine.

The one formatting rule that matters: set the number format of a code column
to text BEFORE writing the value. Do it the other way round and "00003"
silently becomes 3.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

MONEY = "#,##0.00"
TEXT = "@"
REVIEW_FILL = PatternFill("solid", fgColor="FCE4D6")

HEADERS = {
    "store_code": "Store code",
    "store_name": "Store",
    "per_ledger": "Per ledger",
    "per_bank": "Per bank",
    "difference": "Difference",
    "date": "Date",
    "description": "Description",
    "amount": "Amount",
    "value_date": "Value date",
    "source_bank": "Bank",
    "source_file": "Source file",
    "source_line": "Line",
    "matched_by": "Matched by",
    "note": "Note",
}


def _write_table(ws, df: pd.DataFrame, table_name: str, text_cols=("store_code",)):
    """Write a dataframe as a formatted Excel table. Text columns first."""
    cols = list(df.columns)

    for c, col in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=c, value=HEADERS.get(col, col))
        cell.font = Font(bold=True)

    # Number formats go on before any value is written.
    for c, col in enumerate(cols, start=1):
        letter = get_column_letter(c)
        if col in text_cols:
            fmt = TEXT
        elif col in ("per_ledger", "per_bank", "difference", "amount"):
            fmt = MONEY
        else:
            fmt = None
        if fmt:
            for r in range(2, len(df) + 2):
                ws.cell(row=r, column=c).number_format = fmt

    for r, (_, row) in enumerate(df.iterrows(), start=2):
        for c, col in enumerate(cols, start=1):
            value = row[col]
            if col in text_cols:
                value = "" if pd.isna(value) else str(value)
            elif isinstance(value, bool):
                value = "yes" if value else ""
            ws.cell(row=r, column=c, value=value)

    if len(df):
        ref = f"A1:{get_column_letter(len(cols))}{len(df) + 1}"
        table = Table(displayName=table_name, ref=ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True
        )
        ws.add_table(table)

    ws.freeze_panes = "A2"
    for c, col in enumerate(cols, start=1):
        width = max(len(HEADERS.get(col, col)), 12)
        if col in ("description", "note", "store_name"):
            width = 42
        ws.column_dimensions[get_column_letter(c)].width = width


def write(path: Path, comparison: pd.DataFrame, deposits: pd.DataFrame, checks: dict):
    wb = Workbook()

    # --- Comparison: every store, including the ones that agree -------------
    ws = wb.active
    ws.title = "Comparison"
    _write_table(ws, comparison, "ComparisonTable")

    # --- Bank deposits, with the unattributed ones kept visible -------------
    ws = wb.create_sheet("Bank deposits")
    cols = ["store_code", "description", "amount", "value_date",
            "source_bank", "source_line", "matched_by", "note"]
    dep = deposits[cols].copy()
    _write_table(ws, dep, "DepositsTable")

    note_col = cols.index("note") + 1
    for r in range(2, len(dep) + 2):
        if ws.cell(row=r, column=note_col).value:
            for c in range(1, len(cols) + 1):
                ws.cell(row=r, column=c).fill = REVIEW_FILL

    # --- Exceptions: the only sheet anyone should have to act on ------------
    ws = wb.create_sheet("Exceptions")
    exc = comparison[comparison.difference.abs() >= 0.01].copy()
    exc = exc.sort_values("difference", key=abs, ascending=False)
    if len(exc):
        _write_table(ws, exc, "ExceptionsTable")
    else:
        ws["A1"] = "No differences above 0.01."
        ws["A1"].font = Font(bold=True)

    # --- Control totals -----------------------------------------------------
    ws = wb.create_sheet("Checks")
    ws["A1"], ws["B1"] = "Check", "Value"
    ws["A1"].font = ws["B1"].font = Font(bold=True)
    labels = {
        "bank_total": "Bank deposits in scope",
        "attributed": "Attributed to a store",
        "unattributed": "Not attributed (needs review)",
        "ledger_total": "Per store ledgers",
        "net_difference": "Net difference",
        "duplicates_excluded": "Duplicate rows excluded",
        "balanced": "Attribution ties out",
    }
    for i, (key, label) in enumerate(labels.items(), start=2):
        ws.cell(row=i, column=1, value=label)
        value = checks[key]
        cell = ws.cell(row=i, column=2, value="yes" if value is True else
                       ("no" if value is False else value))
        if isinstance(value, float):
            cell.number_format = MONEY
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 18
    ws["A9"] = ("Totals agreeing is a necessary condition, not a sufficient one. "
                "Review the Exceptions sheet before signing off.")
    ws["A9"].alignment = Alignment(wrap_text=True)
    ws.merge_cells("A9:B12")

    wb.save(path)
    return path
