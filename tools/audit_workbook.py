"""Print privacy-safe QA counts from an assessment evidence workbook."""

import argparse
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook


def _records(sheet):
    rows = sheet.iter_rows(values_only=True)
    headers = [str(value or "") for value in next(rows, [])]
    for row in rows:
        yield dict(zip(headers, row))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args()

    workbook = load_workbook(args.workbook, read_only=True, data_only=True)
    print("Sheets:")
    for sheet in workbook.worksheets:
        print(f"  {sheet.title}: {max(sheet.max_row - 1, 0)} data row(s)")

    for sheet_name, field in (
        ("Recommendations", "Disposition"),
        ("App Access Detail", "Flagged Because"),
        ("Admin Role Detail", "Assignment Type"),
        ("Assessment Coverage", "Status"),
    ):
        if sheet_name not in workbook.sheetnames:
            continue
        counts = Counter(str(record.get(field) or "Blank") for record in _records(workbook[sheet_name]))
        print(f"{sheet_name} by {field}:")
        for value, count in counts.most_common():
            print(f"  {value}: {count}")


if __name__ == "__main__":
    main()
