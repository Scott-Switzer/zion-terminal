"""ECB historical fixtures exercise orientation, gaps and hostile archives."""

import importlib
import io
import zipfile

import pytest


def adapter():
    return importlib.import_module("zion_terminal.history_sources.ecb")


def archive(csv, name="eurofxref-hist.csv", extra=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, csv)
        if extra:
            zf.writestr(*extra)
    return out.getvalue()


def parse(csv):
    module = adapter()
    return module.parse(archive(csv), module.tasks()[0])


def test_full_history_task_and_discontinued_currency_orientation():
    task = adapter().tasks()[0]
    assert task == {
        "id": "ecb_fx_full_history",
        "url": "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip",
        "dataset": "ecb_fx",
    }
    rows = parse("Date, USD, CYP, LVL,\n1999-01-04,1.1789,0.58231,0.6668,\n")
    assert rows[0] == {
        "series_id": "ecb:fx:USD:EUR",
        "observation_date": "1999-01-04",
        "value_decimal": "1.1789",
        "missing_reason": None,
        "unit": "USD per EUR",
        "asset_class": "fx",
        "label": "USD per 1 EUR",
    }
    assert {r["series_id"] for r in rows} == {"ecb:fx:USD:EUR", "ecb:fx:CYP:EUR", "ecb:fx:LVL:EUR"}


def test_empty_and_na_are_retained_as_explicit_gaps():
    rows = parse("Date,USD,CYP\n2008-01-02,,N/A\n")
    assert len(rows) == 2
    assert all(r["value_decimal"] is None for r in rows)
    assert [r["missing_reason"] for r in rows] == ["SOURCE_EMPTY", "SOURCE_NA"]


def test_exponent_values_do_not_expand_to_unbounded_output():
    rows = parse("Date,USD\n1999-01-04,1E+99999999\n")
    assert rows[0]["value_decimal"] == "1E+99999999"


def test_identical_duplicates_are_deduplicated_but_conflicts_refused():
    assert len(parse("Date,USD\n1999-01-04,1.1\n1999-01-04,1.10\n")) == 1
    with pytest.raises(ValueError):
        parse("Date,USD\n1999-01-04,1.1\n1999-01-04,1.2\n")


@pytest.mark.parametrize(
    "csv",
    [
        "Date,USD\n1999-02-30,1.2\n",
        "Date,USD\n19990104,1.2\n",
        "Date,USD\n1999-01-04,0\n",
        "Date,USD\n1999-01-04,-1\n",
        "Date,USD\n1999-01-04,NaN\n",
        "Date,USD\n1999-01-04,Infinity\n",
        "Date,USD\n1999-01-04,oops\n",
        "Date,USD\n1999-01-04\n",
        "Date,USD\n1999-01-04,1.2,7\n",
        "Date,USD,USD\n1999-01-04,1,1\n",
        "Date,usd\n1999-01-04,1\n",
        "Date,EUR\n1999-01-04,1\n",
        "Time,USD\n1999-01-04,1.2\n",
        "Date,USD\n",
        "<html>oops</html>",
    ],
)
def test_malformed_values_dates_or_schema_refused(csv):
    with pytest.raises(ValueError):
        parse(csv)


@pytest.mark.parametrize(
    "raw",
    [
        b"<html>gateway error</html>",
        b"PK broken archive",
        archive("Date,USD\n1999-01-04,1.2\n", "../eurofxref-hist.csv"),
        archive("Date,USD\n1999-01-04,1.2\n", extra=("other.csv", "x")),
        archive("x" * (16 * 1024 * 1024 + 1)),
    ],
)
def test_untrusted_archive_members_and_expansion_refused(raw):
    module = adapter()
    with pytest.raises(ValueError):
        module.parse(raw, module.tasks()[0])
