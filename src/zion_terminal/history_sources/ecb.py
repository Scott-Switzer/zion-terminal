"""Parse the ECB's complete euro reference-rate archive without external packages.

Source: https://www.ecb.europa.eu/stats/policy_and_exchange_rates/
euro_reference_exchange_rates/html/index.en.html (Time series CSV).
These informational rates are quoted as currency units per one euro.
"""

import csv
import io
import re
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_COMPRESSED_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
_MEMBER = "eurofxref-hist.csv"


def tasks() -> list[dict]:
    """Return the official full-history download, including retired currencies."""
    return [
        {
            "id": "ecb_fx_full_history",
            "url": "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip",
            "dataset": "ecb_fx",
        }
    ]


def _csv_bytes(raw: bytes) -> bytes:
    if not isinstance(raw, bytes) or len(raw) > MAX_COMPRESSED_BYTES:
        raise ValueError("ECB archive exceeds compressed size limit or is not bytes")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            members = zf.infolist()
            if len(members) != 1 or members[0].filename != _MEMBER:
                raise ValueError("ECB archive must contain only eurofxref-hist.csv")
            info = members[0]
            if info.flag_bits & 1 or info.file_size > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("ECB archive member encrypted or exceeds size limit")
            with zf.open(info) as stream:
                payload = stream.read(MAX_UNCOMPRESSED_BYTES + 1)
            if len(payload) > MAX_UNCOMPRESSED_BYTES or len(payload) != info.file_size:
                raise ValueError("ECB archive member exceeds size limit or is truncated")
            return payload
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise ValueError("Invalid ECB ZIP archive") from exc


def parse(raw: bytes, task: dict) -> list[dict]:
    """Validate a bounded archive and return observations, preserving explicit gaps.

    Identical duplicate observations collapse; conflicting values fail the whole
    archive. Blank and N/A cells remain rows rather than becoming zero or vanishing.
    No allowlist filters out the ECB's historical/discontinued currencies.
    """
    if task.get("dataset") != "ecb_fx":
        raise ValueError("Expected ECB FX task")
    try:
        text = _csv_bytes(raw).decode("utf-8-sig")
        if "\x00" in text or "<html" in text.lower() or "<!doctype" in text.lower():
            raise ValueError("ECB response is not CSV")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = [cell.strip() for cell in next(reader)]
        trailing_empty = header[-1:] == [""]
        if trailing_empty:
            header.pop()
        currencies = header[1:]
        if (
            not header
            or header[0] != "Date"
            or not currencies
            or len(set(currencies)) != len(currencies)
            or any(not re.fullmatch(r"[A-Z]{3}", c) or c == "EUR" for c in currencies)
        ):
            raise ValueError("Invalid ECB CSV header")
        observations: dict[tuple[str, str], dict] = {}
        for row in reader:
            if not row:
                continue
            row = [cell.strip() for cell in row]
            if trailing_empty:
                if len(row) != len(header) + 1 or row[-1] != "":
                    raise ValueError("Invalid ECB trailing field")
                row.pop()
            if len(row) != len(header):
                raise ValueError("ECB row width differs from header")
            day = row[0]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise ValueError("Invalid ECB observation date")
            date.fromisoformat(day)
            for currency, value in zip(currencies, row[1:]):
                missing = "SOURCE_EMPTY" if value == "" else "SOURCE_NA" if value == "N/A" else None
                number = None
                if missing is None:
                    number = Decimal(value)
                    if not number.is_finite() or number <= 0:
                        raise ValueError("ECB FX value must be finite and positive")
                key = (currency, day)
                observation = {
                    "series_id": f"ecb:fx:{currency}:EUR",
                    "observation_date": day,
                    "value_decimal": str(number) if number is not None else None,
                    "missing_reason": missing,
                    "unit": f"{currency} per EUR",
                    "asset_class": "fx",
                    "label": f"{currency} per 1 EUR",
                }
                if key in observations:
                    previous = observations[key]
                    old = previous["value_decimal"]
                    if (Decimal(old) if old is not None else None) != number or previous[
                        "missing_reason"
                    ] != missing:
                        raise ValueError("Conflicting duplicate ECB observation")
                else:
                    observations[key] = observation
        if not observations:
            raise ValueError("ECB archive contains no observations")
        return list(observations.values())
    except (UnicodeError, csv.Error, StopIteration, InvalidOperation) as exc:
        raise ValueError("Invalid ECB CSV content") from exc
