"""Pink Sheet fixture tests; changing date/unit/missing handling must fail these."""
from io import BytesIO
from textwrap import dedent
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from zion_terminal.history_sources.worldbank import WorldBankPinkSheet


def workbook(prices=None, indices=None):
    prices = prices or [
        ['World Bank Commodity Price Data'],
        ['monthly prices in nominal US dollars, 1960 to present'],
        [None, 'Crude oil, WTI', 'Gold'],
        [None, '($/bbl)', '($/troy oz)'],
        ['1960M01', '…', '35.27'],
        ['2020M04', '-37.63', None],
        ['2020M05', '0', 'NA'],
    ]
    indices = indices or [
        ['monthly indices based on nominal US dollars, 2010=100'],
        [None, 'Total Index'],
        [None, None, 'Energy **'],
        ['1960M01', '7.6', '2.1'],
    ]
    out = BytesIO()
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with ZipFile(out, 'w') as z:
        z.writestr('xl/workbook.xml', dedent(f"""
            <workbook xmlns="{ns}"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets>
                <sheet name="Monthly Prices" sheetId="1" r:id="rId1"/>
                <sheet name="Monthly Indices" sheetId="2" r:id="rId2"/>
              </sheets>
            </workbook>"""))
        z.writestr('xl/_rels/workbook.xml.rels', dedent("""
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="worksheets/sheet1.xml"/>
              <Relationship Id="rId2" Target="/xl/worksheets/sheet2.xml"/>
            </Relationships>"""))
        for i, rows in enumerate([prices, indices], 1):
            xml = f'<worksheet xmlns="{ns}"><sheetData>'
            for r, row in enumerate(rows, 1):
                xml += f'<row r="{r}">'
                for c, value in enumerate(row):
                    if value is not None:
                        xml += (f'<c r="{chr(65+c)}{r}" t="inlineStr">'
                                f'<is><t>{escape(value)}</t></is></c>')
                xml += '</row>'
            z.writestr(f'xl/worksheets/sheet{i}.xml', xml + '</sheetData></worksheet>')
    return out.getvalue()


def test_months_prices_indexes_units_and_missing_are_preserved():
    source = WorldBankPinkSheet()
    rows = source.parse(workbook(), source.tasks()[0])
    assert len(rows) == 8
    first = rows[0]
    assert first == {'series_id': 'worldbank:commodity:crude_oil_wti',
                     'observation_date': '1960-01-01', 'value_decimal': None,
                     'missing_reason': 'SOURCE_MISSING', 'unit': '($/bbl)',
                     'asset_class': 'commodities', 'label': 'Crude oil, WTI',
                     'frequency': 'monthly'}
    assert rows[1]['value_decimal'] == '35.27'
    assert rows[2]['value_decimal'] == '-37.63'
    assert rows[3]['missing_reason'] == 'SOURCE_MISSING'
    assert rows[4]['value_decimal'] == '0'
    assert rows[5]['missing_reason'] == 'SOURCE_MISSING'
    assert rows[6]['series_id'] == 'worldbank:commodity:total_index'
    assert rows[6]['unit'] == 'index (2010=100)'
    assert rows[7]['series_id'] == 'worldbank:commodity:energy_index'


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', 'nonsense', '=1+1', '#VALUE!'])
def test_refuses_nonfinite_malformed_or_formula_values(bad):
    raw = workbook(prices=[['title'], [None, 'Gold'], [None, '($/troy oz)'],
                           ['1960M01', bad]])
    with pytest.raises(ValueError):
        WorldBankPinkSheet().parse(raw, {})


@pytest.mark.parametrize('raw', [b'', b'<html>Access denied</html>', b'PKgarbage'])
def test_refuses_non_xlsx(raw):
    with pytest.raises(ValueError):
        WorldBankPinkSheet().parse(raw, {})


@pytest.mark.parametrize('period', ['1960M13', '1960M00', '1960M1'])
def test_refuses_malformed_months(period):
    raw = workbook(prices=[['title'], [None, 'Gold'], [None, '($/troy oz)'],
                           [period, '1']])
    with pytest.raises(ValueError):
        WorldBankPinkSheet().parse(raw, {})


def test_refuses_missing_units_and_duplicate_months():
    for rows in [
        [['title'], [None, 'Gold'], [None, None], ['1960M01', '1']],
        [['title'], [None, 'Gold'], [None, '($/oz)'], ['1960M01', '1'],
         ['1960M01', '2']],
    ]:
        with pytest.raises(ValueError):
            WorldBankPinkSheet().parse(workbook(prices=rows), {})


def test_real_excel_error_is_an_explicit_gap():
    raw = workbook()
    out = BytesIO()
    with ZipFile(BytesIO(raw)) as src, ZipFile(out, 'w') as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == 'xl/worksheets/sheet1.xml':
                data = data.replace(b'<c r="B5" t="inlineStr"><is><t>',
                                    b'<c r="B5" t="e"><v>').replace(
                    '…</t></is></c>'.encode(), b'#VALUE!</v></c>')
            dst.writestr(name, data)
    rows = WorldBankPinkSheet().parse(out.getvalue(), {})
    assert rows[0]['value_decimal'] is None
    assert rows[0]['missing_reason'] == 'SOURCE_CELL_ERROR'


def test_module_adapter_contract():
    from zion_terminal.history_sources import worldbank

    task = worldbank.tasks()[0]
    assert task['dataset'] == 'worldbank_commodities'
    assert task['url'].startswith('https://thedocs.worldbank.org/')
    assert task['url'].endswith('CMO-Historical-Data-Monthly.xlsx')
    assert worldbank.parse(workbook(), task)[0]['observation_date'] == '1960-01-01'
