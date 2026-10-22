"""World Bank Pink Sheet monthly observations, not tradable futures or PIT data.

Source: https://www.worldbank.org/en/research/commodity-markets
License reference: https://datacatalog.worldbank.org/public-licenses
The catalog describes CC BY 4.0 as the default for World Bank-produced open
datasets, with third-party exceptions. Retain source attribution and review
source-specific rights before redistribution. First-of-month dates identify the
observed monthly period, never the publication or historical availability date.

Uses only the standard library, preserving source XML decimals without a float
round trip. Hidden comparison sheets and descriptions are deliberately excluded.
"""

from __future__ import annotations

import io
import posixpath
import re
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/"
    "CMO-Historical-Data-Monthly.xlsx"
)
_MISSING = {"", "…", "...", "..", "NA", "N/A", "-", "--"}


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _cells(archive: ZipFile, path: str, strings: list[str]) -> list[dict[str, str]]:
    rows = []
    for row in ET.fromstring(archive.read(path)).findall("m:sheetData/m:row", _NS):
        values = {}
        for cell in row.findall("m:c", _NS):
            column = re.sub(r"\d", "", cell.attrib["r"])
            if cell.find("m:f", _NS) is not None:
                raise ValueError("Pink Sheet formula cells are unsupported")
            kind = cell.get("t")
            value = cell.find("m:v", _NS)
            text = value.text if value is not None and value.text else ""
            if kind == "s":
                text = strings[int(text)]
            elif kind == "inlineStr":
                text = "".join(cell.find("m:is", _NS).itertext())
            elif kind == "e":
                text = "__SOURCE_CELL_ERROR__"
            elif kind == "b":
                raise ValueError("Pink Sheet boolean cell")
            values[column] = text.strip()
        rows.append(values)
    return rows


def _sheet_records(rows: list[dict[str, str]], indices: bool) -> list[dict]:
    start = next(
        (i for i, r in enumerate(rows) if re.fullmatch(r"\d{4}M\d{2}", r.get("A", ""))), None
    )
    if start is None or start < 2:
        raise ValueError("Pink Sheet monthly table/header not found")
    header = rows[:start]
    if indices:
        title = " ".join(r.get("A", "") for r in header)
        match = re.search(r"\b(\d{4})\s*=\s*100\b", title)
        if not match:
            raise ValueError("Pink Sheet index base year missing")
        unit = f"index ({match.group(1)}=100)"
        columns = {}
        for row in header:
            for column, text in row.items():
                if column != "A" and text:
                    columns[column] = (text, unit)
    else:
        labels, units = header[-2:]
        columns = {}
        for column, label in labels.items():
            if column == "A" or not label:
                continue
            unit = units.get(column)
            if not unit:
                raise ValueError(f"Pink Sheet unit missing: {label}")
            columns[column] = (label, unit)
    if not columns:
        raise ValueError("Pink Sheet series columns missing")
    records, seen, series_ids = [], set(), set()
    descriptors = {}
    for column, (label, unit) in columns.items():
        slug = _slug(label)
        if indices and not slug.endswith("index"):
            slug += "_index"
        series_id = "worldbank:commodity:" + slug
        if not slug or series_id in series_ids:
            raise ValueError("Pink Sheet duplicate/empty series name")
        series_ids.add(series_id)
        descriptors[column] = {
            "series_id": series_id,
            "unit": unit,
            "label": label,
            "asset_class": "commodities",
            "frequency": "monthly",
        }
    for row in rows[start:]:
        period = row.get("A", "")
        if not re.fullmatch(r"\d{4}M\d{2}", period):
            if re.match(r"^\d{4}", period) or any(row.get(c) for c in columns):
                raise ValueError("Pink Sheet malformed monthly row")
            continue
        year, month = int(period[:4]), int(period[-2:])
        if not (1 <= year <= 9999 and 1 <= month <= 12) or period in seen:
            raise ValueError("Pink Sheet invalid/duplicate monthly period")
        seen.add(period)
        for column, descriptor in descriptors.items():
            raw = row.get(column, "")
            cell_error = raw == "__SOURCE_CELL_ERROR__"
            missing = raw.upper() in _MISSING or cell_error
            value = None
            if not missing:
                try:
                    number = Decimal(raw)
                except InvalidOperation as exc:
                    raise ValueError("Pink Sheet malformed numeric value") from exc
                if not number.is_finite():
                    raise ValueError("Pink Sheet nonfinite numeric value")
                value = format(number, "f")
            records.append(
                {
                    **descriptor,
                    "observation_date": f"{year:04}-{month:02}-01",
                    "value_decimal": value,
                    "missing_reason": (
                        "SOURCE_CELL_ERROR" if cell_error else "SOURCE_MISSING" if missing else None
                    ),
                }
            )
    return records


class WorldBankPinkSheet:
    """A single official XLSX task containing nominal prices and base-year indices."""

    def tasks(self) -> list[dict]:
        return [
            {"id": "worldbank-pink-sheet-monthly", "url": _URL, "dataset": "worldbank_commodities"}
        ]

    def parse(self, raw: bytes, task: dict) -> list[dict]:
        if not raw.startswith(b"PK"):
            raise ValueError("World Bank source is not an XLSX workbook")
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                strings = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    strings = [
                        "".join(s.itertext())
                        for s in ET.fromstring(archive.read("xl/sharedStrings.xml"))
                    ]
                rels = {
                    r.get("Id"): r.get("Target")
                    for r in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                }
                sheets = ET.fromstring(archive.read("xl/workbook.xml"))
                result, found = [], set()
                for sheet in sheets.findall("m:sheets/m:sheet", _NS):
                    name = sheet.get("name")
                    if name not in {"Monthly Prices", "Monthly Indices"}:
                        continue
                    target = rels[sheet.get(f"{{{_REL}}}id")]
                    path = (
                        posixpath.normpath("xl/" + target)
                        if not target.startswith("/")
                        else target.lstrip("/")
                    )
                    result.extend(
                        _sheet_records(_cells(archive, path, strings), name == "Monthly Indices")
                    )
                    found.add(name)
                if found != {"Monthly Prices", "Monthly Indices"}:
                    raise ValueError("World Bank required monthly worksheets missing")
                if len({(r["series_id"], r["observation_date"]) for r in result}) != len(result):
                    raise ValueError("World Bank overlapping price/index series")
                return result
        except (BadZipFile, ET.ParseError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("Malformed World Bank XLSX workbook") from exc


def tasks() -> list[dict]:
    """Return the official monthly workbook acquisition task."""
    return WorldBankPinkSheet().tasks()


def parse(raw: bytes, task: dict) -> list[dict]:
    """Parse monthly commodity prices and explicitly denominated indices."""
    return WorldBankPinkSheet().parse(raw, task)
