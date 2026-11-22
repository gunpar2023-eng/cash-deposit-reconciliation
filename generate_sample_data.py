"""
Generate synthetic input data for the reconciliation engine.

Everything here is invented. No real store, bank, person or amount is used.
The generator deliberately injects the failure modes the engine has to survive:
  - a store code written without its leading zero
  - a deposit slip carrying a person's name instead of a store code
  - the same deposit appearing twice in two files from the SAME bank (duplicate)
  - the same store depositing at two DIFFERENT banks on one day (legitimate, must sum)
  - a deposit that cannot be attributed to any store at all
  - outgoing items (fees, transfers) that must not be counted as deposits
  - amounts stored as text with a comma decimal separator
"""

import random
from pathlib import Path

import pandas as pd

RNG = random.Random(20260118)
OUT = Path(__file__).parent / "sample_data"

STORE_NAMES = [
    "Riverside", "Oakfield", "Northgate", "Harbour Point", "Elm Street",
    "Castle Park", "Greenway", "Southbridge", "Kings Cross", "Maple Court",
    "Westbrook", "Stonehill", "Fairview", "Lakeside", "Redwood Plaza",
    "Victoria Road", "Brookfield", "Highgate", "Marble Arch", "Pinecrest",
]

STAFF_FIRST = ["Anna", "Peter", "Maria", "Tomas", "Elena", "Jakob", "Sofia", "Lukas"]
STAFF_LAST = ["Berg", "Novak", "Keller", "Lindqvist", "Marek", "Vogel", "Halls", "Rossi"]

BANKS = ["NORTHBANK", "MERIDIAN"]


def store_code(i: int) -> str:
    """Five-digit, zero-padded. The leading zero is the whole point."""
    return f"{i + 1:05d}"


def build_master():
    rows = []
    for i, name in enumerate(STORE_NAMES):
        rows.append({"store_code": store_code(i), "store_name": name})
    return pd.DataFrame(rows)


def build_staff(master):
    """Staff roster: links a person to the store they work at."""
    rows = []
    for _, s in master.iterrows():
        for _ in range(2):
            rows.append({
                "full_name": f"{RNG.choice(STAFF_FIRST)} {RNG.choice(STAFF_LAST)}",
                "store_code": s.store_code,
                "phone": f"+3715{RNG.randint(1000000, 9999999)}",
            })
    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset="full_name").reset_index(drop=True)


def build_ledger(master, date):
    """The store-side cash ledger. 'deposited_to_bank' is negative (money leaving the till)."""
    rows = []
    for _, s in master.iterrows():
        opening = round(RNG.uniform(200, 1500), 2)
        cash_in = round(RNG.uniform(400, 4000), 2)
        refunds = round(RNG.uniform(0, 120), 2)
        expenses = round(RNG.uniform(0, 90), 2)

        # Most stores bank everything above a float; a few bank nothing.
        if RNG.random() < 0.12:
            deposited = 0.0
        else:
            deposited = -round((opening + cash_in - refunds - expenses) * RNG.uniform(0.6, 0.95), 2)

        closing = round(opening + cash_in - refunds - expenses + deposited, 2)
        counted = round(closing + RNG.choice([0, 0, 0, -0.5, 1.0, -3.2]), 2)

        rows.append({
            "store_code": s.store_code,
            "store_name": s.store_name,
            "opening_balance": opening,
            "cash_in": cash_in,
            "refunds_out": refunds,
            "expenses_out": expenses,
            "deposited_to_bank": deposited,
            "closing_balance": closing,
            "counted": counted,
            "till_variance": round(counted - closing, 2),
            "date": date,
        })
    return pd.DataFrame(rows)


def build_bank_files(ledger, staff, date):
    """
    Produce several bank statement files. Each row is one movement.
    Deposits are positive; fees and transfers are negative and must be excluded.
    """
    files = {}
    deposits = ledger[ledger.deposited_to_bank < 0].copy()

    primary, secondary, duplicate = [], [], []

    for idx, (_, row) in enumerate(deposits.iterrows()):
        amount = abs(row.deposited_to_bank)
        code = row.store_code

        if idx == 2:
            # Leading zero lost somewhere upstream: "00003" arrives as "3".
            ref = str(int(code))
            name = row.store_name
        elif idx == 5:
            # Slip carries the depositing employee's name, no code at all.
            person = staff[staff.store_code == code].iloc[0]
            ref = ""
            name = person.full_name
        elif idx == 7:
            # Split across two different banks on the same day -> must be SUMMED.
            half = round(amount / 2, 2)
            primary.append(_dep(code, row.store_name, half, date))
            secondary.append(_dep(code, row.store_name, round(amount - half, 2), date))
            continue
        elif idx == 11:
            # A genuine break: the store banked less than its ledger claims.
            primary.append(_dep(code, row.store_name, round(amount - 250.00, 2), date))
            continue
        elif idx == 13:
            # A genuine break: the ledger shows a deposit the bank never received.
            continue
        else:
            ref = code
            name = row.store_name

        rec = _dep(ref, name, amount, date)
        primary.append(rec)

        if idx == 9:
            # Same bank re-exported the same movement -> must NOT be summed.
            duplicate.append(dict(rec))

    # A deposit nobody can attribute. The engine must flag it, not guess.
    primary.append(_dep("", "CASH DEPOSIT TERMINAL 4471", 612.40, date))

    # Outgoing items that must be filtered out.
    primary.append(_out("MONTHLY ACCOUNT FEE", -18.50, date))
    primary.append(_out("OUTGOING TRANSFER", -2400.00, date))
    secondary.append(_out("CARD SETTLEMENT FEE", -42.75, date))

    files["NORTHBANK_part1.csv"] = pd.DataFrame(primary)
    files["NORTHBANK_part2.csv"] = pd.DataFrame(duplicate)
    files["MERIDIAN.csv"] = pd.DataFrame(secondary)
    return files


def _dep(ref, name, amount, date):
    return {
        "reference": ref,
        "description": f"CASH DEPOSIT {name}".strip(),
        # Amounts arrive as text with a comma decimal separator, as bank exports often do.
        "amount": f"{amount:.2f}".replace(".", ","),
        "value_date": date,
    }


def _out(desc, amount, date):
    return {
        "reference": "",
        "description": desc,
        "amount": f"{amount:.2f}".replace(".", ","),
        "value_date": date,
    }


def main():
    date = "2026-01-15"
    OUT.mkdir(exist_ok=True)

    master = build_master()
    staff = build_staff(master)
    ledger = build_ledger(master, date)
    banks = build_bank_files(ledger, staff, date)

    master.to_csv(OUT / "store_master.csv", index=False)
    staff.to_csv(OUT / "staff_roster.csv", index=False)
    ledger.to_csv(OUT / "cash_ledger.csv", index=False)
    for fname, df in banks.items():
        df.to_csv(OUT / fname, index=False)

    print(f"Wrote synthetic data to {OUT}/")
    for p in sorted(OUT.iterdir()):
        print(f"  {p.name:<24} {len(pd.read_csv(p)):>4} rows")


if __name__ == "__main__":
    main()
