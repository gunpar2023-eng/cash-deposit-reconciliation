"""Render a PNG preview of the output workbook for the README."""
import asyncio
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from playwright.async_api import async_playwright

HERE = Path(__file__).parent


def sheet_to_rows(ws, limit=None):
    rows = list(ws.iter_rows(values_only=True))
    header, body = rows[0], rows[1:]
    if limit:
        body = body[:limit]
    return header, body


MONEY_COLS = {"Per ledger", "Per bank", "Difference"}


def fmt(v, header=None):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)) and header in MONEY_COLS:
        return f"{v:,.2f}"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return "" if v is None else str(v)


def table_html(header, body, highlight_col=None):
    th = "".join(f"<th>{h}</th>" for h in header)
    trs = []
    for row in body:
        tds = []
        for i, v in enumerate(row):
            cls = ""
            if (highlight_col is not None and i == highlight_col
                    and isinstance(v, (int, float)) and not isinstance(v, bool)
                    and abs(v) >= 0.01):
                cls = ' class="neg"' if v < 0 else ' class="pos"'
            tds.append(f"<td{cls}>{fmt(v, header[i] if i < len(header) else None)}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"


async def main():
    wb = load_workbook(HERE / "reconciliation.xlsx")

    ch, cb = sheet_to_rows(wb["Comparison"], limit=8)
    eh, eb = sheet_to_rows(wb["Exceptions"])
    kh, kb = sheet_to_rows(wb["Checks"])
    kb = [r for r in kb if r[0]]

    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
           background:#fff; margin:0; padding:28px; width:1100px; color:#1a1a1a; }}
    h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em;
          color:#6b7280; margin:26px 0 10px; font-weight:600; }}
    h2:first-child {{ margin-top:0; }}
    table {{ border-collapse:collapse; width:100%; font-size:12.5px; }}
    th {{ background:#1f3864; color:#fff; text-align:left; padding:7px 10px;
          font-weight:600; white-space:nowrap; }}
    td {{ padding:6px 10px; border-bottom:1px solid #e5e7eb; white-space:nowrap; }}
    tbody tr:nth-child(even) {{ background:#f6f8fc; }}
    td.neg {{ color:#b91c1c; font-weight:600; }}
    td.pos {{ color:#166534; font-weight:600; }}
    .checks td:first-child {{ width:60%; }}
    .note {{ font-size:11.5px; color:#6b7280; margin-top:8px; font-style:italic; }}
    </style></head><body>
    <h2>Comparison &mdash; every store, including the ones that agree</h2>
    {table_html(ch, cb, highlight_col=4)}
    <p class="note">First 8 of 20 rows.</p>
    <h2>Exceptions &mdash; the only sheet anyone has to act on</h2>
    {table_html(eh, eb, highlight_col=4)}
    <h2>Control totals</h2>
    <div class="checks">{table_html(kh, kb)}</div>
    </body></html>"""

    (HERE / "_preview.html").write_text(html)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1100, "height": 900},
                                      device_scale_factor=2)
        await page.goto(f"file://{HERE / '_preview.html'}")
        await page.screenshot(path=str(HERE / "docs" / "output-preview.png"), full_page=True)
        await browser.close()
    print("wrote docs/output-preview.png")


asyncio.run(main())
