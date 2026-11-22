"""
Tests for the cases that caused wrong answers in practice.

Each test here exists because the naive version of the code got it wrong.
"""

import pandas as pd
import pytest

from reconcile import (
    MatchIndex,
    flag_duplicates,
    is_outgoing,
    normalise_code,
    parse_amount,
    reconcile,
)


@pytest.fixture
def master():
    return pd.DataFrame([
        {"store_code": "00003", "store_name": "Northgate"},
        {"store_code": "01600", "store_name": "Harbour Point"},
        {"store_code": "00042", "store_name": "Elm Street"},
    ])


@pytest.fixture
def staff():
    return pd.DataFrame([
        {"full_name": "Anna Berg", "store_code": "00042", "phone": "+37150000000"},
    ])


@pytest.fixture
def index(master, staff):
    return MatchIndex.build(master, staff)


# --- codes ----------------------------------------------------------------

def test_leading_zero_is_preserved():
    assert normalise_code("3") == "00003"
    assert normalise_code("00003") == "00003"
    assert normalise_code(3) == "00003"


def test_code_with_prefix_letter():
    assert normalise_code("M1600") == "01600"


def test_code_with_no_digits_is_empty():
    assert normalise_code("CASH DEPOSIT") == ""


# --- amounts --------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1234,56", 1234.56),      # comma decimal
    ("1.234,56", 1234.56),     # dot thousands, comma decimal
    ("1,234.56", 1234.56),     # comma thousands, dot decimal
    ("1234.56", 1234.56),
    ("", 0.0),
    ("n/a", 0.0),
])
def test_amount_parsing_survives_locales(raw, expected):
    assert parse_amount(raw) == expected


# --- exclusions -----------------------------------------------------------

@pytest.mark.parametrize("desc", [
    "MONTHLY ACCOUNT FEE", "OUTGOING TRANSFER", "CARD SETTLEMENT FEE",
])
def test_outgoing_items_are_excluded(desc):
    assert is_outgoing(desc)


def test_a_plain_deposit_is_not_excluded():
    assert not is_outgoing("CASH DEPOSIT Northgate")


# --- matching -------------------------------------------------------------

def test_name_beats_a_truncated_code(index):
    """
    The row says code 1600 but names Northgate, which is 00003.
    Trusting the code here is the silent-failure case.
    """
    code, how = index.lookup("1600", "CASH DEPOSIT Northgate")
    assert code == "00003"
    assert how == "name"


def test_clean_code_is_used_when_no_name_present(index):
    code, how = index.lookup("01600", "CASH DEPOSIT TERMINAL 9")
    assert code == "01600"
    assert how == "code"


def test_staff_name_resolves_to_their_store(index):
    code, how = index.lookup("", "CASH DEPOSIT Anna Berg")
    assert code == "00042"
    assert how == "staff"


def test_unattributable_row_is_not_guessed(index):
    code, how = index.lookup("", "CASH DEPOSIT TERMINAL 4471")
    assert code == ""
    assert how == "REVIEW"


# --- duplicates -----------------------------------------------------------

def _dep(bank, code, amount):
    return {
        "source_bank": bank, "store_code": code, "amount": amount,
        "value_date": "2026-01-15", "description": "", "source_file": "x.csv",
        "source_line": 2, "matched_by": "code",
    }


def test_same_bank_repeat_is_a_duplicate():
    df = pd.DataFrame([_dep("NORTHBANK", "00003", 100.0),
                       _dep("NORTHBANK", "00003", 100.0)])
    out = flag_duplicates(df)
    assert out.is_duplicate.tolist() == [False, True]


def test_different_banks_same_day_are_both_real():
    df = pd.DataFrame([_dep("NORTHBANK", "00003", 100.0),
                       _dep("MERIDIAN", "00003", 100.0)])
    out = flag_duplicates(df)
    assert out.is_duplicate.tolist() == [False, False]


# --- reconciliation -------------------------------------------------------

def test_split_deposits_across_banks_are_summed():
    ledger = pd.DataFrame([{
        "store_code": "00003", "store_name": "Northgate",
        "deposited_to_bank": -200.0, "date": "2026-01-15",
    }])
    deposits = flag_duplicates(pd.DataFrame([
        _dep("NORTHBANK", "00003", 120.0),
        _dep("MERIDIAN", "00003", 80.0),
    ]))
    out = reconcile(ledger, deposits)
    assert out.per_bank.iloc[0] == 200.0
    assert out.difference.iloc[0] == 0.0


def test_store_with_no_deposit_still_appears():
    """Reconciliation means every store, not only the interesting ones."""
    ledger = pd.DataFrame([{
        "store_code": "00099", "store_name": "Quiet Branch",
        "deposited_to_bank": 0.0, "date": "2026-01-15",
    }])
    deposits = flag_duplicates(pd.DataFrame([_dep("NORTHBANK", "00003", 50.0)]))
    out = reconcile(ledger, deposits)
    assert len(out) == 1
    assert out.per_bank.iloc[0] == 0.0


def test_shortfall_is_reported_as_negative():
    ledger = pd.DataFrame([{
        "store_code": "00003", "store_name": "Northgate",
        "deposited_to_bank": -500.0, "date": "2026-01-15",
    }])
    deposits = flag_duplicates(pd.DataFrame([_dep("NORTHBANK", "00003", 250.0)]))
    out = reconcile(ledger, deposits)
    assert out.difference.iloc[0] == -250.0
