import asyncio
import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

sys.path[:0] = [
    str(Path(__file__).parents[2] / "deploy/cloudflare-query/src"),
    str(Path(__file__).parents[2] / "scripts"),
]


def module():
    return importlib.import_module("unified_research_api")


@pytest.fixture
def fixture(monkeypatch):
    api = module()
    from materialize_sec_engine import materialize
    from sec_engine import history

    store, gets = {}, []
    prefix = "gold/unified-research/releases/" + "a" * 64 + "/"

    def pin(name, value):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        key = prefix + name
        store[key] = raw
        return {"key": key, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    financial = {
        "release_id": "b" * 64,
        "registry_sha256": "c" * 64,
        "maximum_as_of": "2026-09-24T00:00:00Z",
        "date_from": "2014-01-01",
        "date_to": "2026-09-24",
    }
    data = {}
    for cik in ["0000104169", "0000078003"]:

        def records(values):
            return {
                "units": {
                    "USD": [
                        {
                            "start": year + "-01-01",
                            "end": year + "-12-31",
                            "val": value,
                            "accn": cik + "-19-000001" if year == "2018" else cik + "-18-000001",
                            "filed": "2019-02-01" if year == "2018" else "2018-02-01",
                            "form": "10-K",
                        }
                        for year, value in values
                    ]
                }
            }

        facts = {
            name: records(values)
            for name, values in {
                "Revenues": [("2017", 2), ("2018", 3)],
                "OperatingIncomeLoss": [("2018", 1)],
                "NetIncomeLoss": [("2018", 1)],
                "NetCashProvidedByUsedInOperatingActivities": [("2018", 2)],
                "PaymentsToAcquirePropertyPlantAndEquipment": [("2018", 1)],
            }.items()
        }
        raw = json.dumps(
            {"cik": int(cik), "entityName": "Synthetic SEC issuer", "facts": {"us-gaap": facts}}
        ).encode()
        data[cik] = materialize(
            raw, cik, {}, {}, financial["release_id"], financial["registry_sha256"]
        )

    async def fin_root(env):
        return financial

    async def entry(env, m, cik):
        if cik not in data:
            api.fail("CIK_NOT_IN_SOURCE_ARCHIVE", 404)
        return {"materialized": {"source_release_id": financial["release_id"]}}

    async def company(env, m, cik, e):
        return data[cik]

    monkeypatch.setattr(api.sec, "root", fin_root)
    monkeypatch.setattr(api.sec, "entry", entry)
    monkeypatch.setattr(api.sec, "company", company)
    certs = {}
    anchors = []
    symbols = {}
    for symbol, cik, day in [
        ("WMT", "0000104169", "2019-12-04"),
        ("PFE", "0000078003", "2019-11-07"),
    ]:
        anchors.append(
            {
                "symbol": symbol,
                "cik": cik,
                "observed_on": day,
                "valid_from": day,
                "valid_to": day,
                "identity_status": "POINT_SUPPORTED_FILING_COVER_ONLY",
                "known_at": None,
                "exchange_name": "Synthetic exchange",
                "dimensions": [],
            }
        )
        payload = {
            "schema_version": "owner-market-history-v1",
            "rights": "PRIVATE_OWNER_ALPACA_RESEARCH_ONLY",
            "provider_symbol": symbol,
            "security_id": "synthetic-source-symbol:" + symbol,
            "numeric_representation": "ORIGINAL_RAW_DECIMAL_TOKEN_VERIFIED",
            "price_basis": "RAW",
            "adjustment_method": "NONE",
            "observations": [
                {
                    "session_date": day,
                    "close": "9007199254740993.0001",
                    "currency": "USD",
                    "source_currency": "UNKNOWN",
                    "currency_evidence": {"currency": "USD"},
                    "market_available_at": None,
                    "source_record_ids": ["synthetic-market-" + symbol],
                }
            ],
            "gaps": [],
            "source_records": {
                "synthetic-market-" + symbol: {
                    "source_page_sha256": "synthetic-page",
                    "session_date": day,
                }
            },
            "source_pages": {"synthetic-page": {"currency_evidence": {"currency": "USD"}}},
        }
        symbols[symbol] = pin(symbol + ".json", payload)
        h = history(
            data[cik],
            metrics=[
                "revenue",
                "operating_income",
                "net_income",
                "operating_cash_flow",
                "capital_expenditures",
                "free_cash_flow",
            ],
            frequency="annual",
            date_from="2014-01-01",
            date_to=day,
            as_of=day + "T00:00:00Z",
        )
        proof = {
            "as_of": day + "T00:00:00Z",
            "member_sha256": data[cik]["member_sha256"],
            "registry_sha256": financial["registry_sha256"],
            "implementation_sha256": "d" * 64,
            "method": "SYNTHETIC_INDEPENDENT_CHECK",
            "checks": [
                {
                    k: r.get(k)
                    for k in [
                        "metric",
                        "frequency",
                        "period_start",
                        "period_end",
                        "unit",
                        "value_decimal",
                    ]
                }
                | {"status": "PASS", "source_ids": r["source_observation_ids"]}
                for r in h["observations"]
            ],
        }
        companion = {
            "schema_version": "sec-metric-accounting-companion-v1",
            "rights": "PUBLIC_SEC_ONLY",
            "member_sha256": data[cik]["member_sha256"],
            "registry_sha256": financial["registry_sha256"],
            "observations": [],
            "accounting_inputs": [],
            "as_of": financial["maximum_as_of"],
            "historical_reconciliation": {day + "T00:00:00Z": proof},
        }
        certs[cik] = pin(cik + ".cert.json", companion) | {
            "member_sha256": data[cik]["member_sha256"],
            "registry_sha256": financial["registry_sha256"],
        }
    identity = pin(
        "identity.json",
        {"rights": "PUBLIC_SEC_ONLY", "identity_anchors": anchors, "outstanding_shares": []},
    )
    market = pin(
        "market.json",
        {
            "schema_version": "owner-market-release-v1",
            "release_id": "e" * 64,
            "rights": "PRIVATE_OWNER_ALPACA_RESEARCH_ONLY",
            "symbols": symbols,
        },
    )
    research = {
        "schema_version": "unified-research-release-v1",
        "research_release_id": "a" * 64,
        "financial_release_id": financial["release_id"],
        "certifications": certs,
        "identity": identity,
        "market": market,
        "source_policy": {
            "public_sec": True,
            "owner_market": "PRIVATE_OWNER_ALPACA_RESEARCH_ONLY",
            "human_research_only": True,
            "source_prices_not_llm": True,
            "licensed_wrds_allowed": False,
        },
    }
    rootref = pin("root.json", research)

    class Obj:
        def __init__(self, raw):
            self.raw = raw
            self.size = len(raw)
            self.consumed = False

        async def text(self):
            if self.consumed:
                raise RuntimeError("BODY_ALREADY_CONSUMED")
            self.consumed = True
            return self.raw.decode()

    class Bucket:
        async def get(self, key):
            gets.append(key)
            return Obj(store[key]) if key in store else None

        async def put(self, *args):
            raise AssertionError("NO CLOUD WRITES")

    env = SimpleNamespace(
        MARKET_DATA=Bucket(),
        ZION_PRIVATE_API_TOKEN="synthetic-secret",
        UNIFIED_RESEARCH_MANIFEST_KEY=rootref["key"],
        UNIFIED_RESEARCH_MANIFEST_SHA256=rootref["sha256"],
        SEC_ENGINE_MANIFEST_SHA256="b" * 64,
    )

    def query(action, params=None, token="synthetic-secret", method="GET"):
        req = SimpleNamespace(
            url="https://test/private/v1/research-" + action + "?" + urlencode(params or {}),
            method=method,
            headers={"authorization": "Bearer " + token},
        )
        return asyncio.run(api.handle(env, req))

    return api, query, research, store, gets, financial, data, rootref


def test_exact_sec_valuation_has_complete_sources_and_no_market_invention(fixture):
    _, query, _, _, _, _, _, _ = fixture
    result, status = query(
        "valuation",
        {"cik": "0000104169", "metrics": "operating_margin,net_margin,revenue_growth,market_cap"},
    )
    assert status == 200, result
    margin = next(r for r in result["observations"] if r["metric"] == "operating_margin")
    assert margin["numerator"] == "1" and margin["denominator"] == "3"
    cap = next(r for r in result["observations"] if r["metric"] == "market_cap")
    assert cap["quality"] == "UNAVAILABLE" and cap["value_decimal"] is None
    ids = {sid for row in result["observations"] for sid in row.get("source_ids", [])}
    assert ids <= set(result["source_records"])
    assert result["research_release_id"] == "a" * 64 and result["release_id"] == "b" * 64


def test_source_symbol_prices_retrospective_and_explicit_pit_rejected(fixture):
    _, query, _, _, gets, _, _, _ = fixture
    result, status = query("prices", {"symbol": "WMT"})
    assert status == 200 and result["prices"][0]["value_decimal"] == "9007199254740993.0001"
    assert result["prices"][0]["available_at"] is None
    assert result["prices"][0]["currency"] == "USD"
    assert query("prices", {"symbol": "WMT", "as_of": "2019-12-04T00:00:00Z"})[1] == 422
    assert query("prices", {"cik": "0000104169"})[1] == 422


def test_combined_screen_two_exact_points_uses_historical_proofs(fixture):
    _, query, _, _, _, _, _, _ = fixture
    result, status = query(
        "screen",
        {
            "ciks": "0000104169,0000078003",
            "fundamental_metric": "revenue_growth",
            "fundamental_minimum": "0.1",
            "market_metric": "close",
            "market_minimum": "0",
        },
    )
    assert status == 200, result
    assert result["total_matches"] == 2
    assert all(
        row["financial_as_of"] == row["price_date"] + "T00:00:00Z" for row in result["matches"]
    )
    assert all(row["identity_known_at"] is None for row in result["matches"])
    assert "RETROSPECTIVE" in result["qualification"]


def test_auth_strict_queries_versions_and_read_budget(fixture, monkeypatch):
    api, query, _, _, gets, _, _, _ = fixture
    assert query("overview", {"cik": "0000104169"}, token="wrong")[1] == 401
    assert not gets
    assert query("overview", {"cik": "0000104169", "sql": "SELECT"})[1] == 400
    assert query("overview", {"cik": "0000104169", "expected_release_id": "wrong"})[1] == 409
    monkeypatch.setattr(api, "MAX_SOURCE_BYTES", 10)
    assert query("overview", {"cik": "0000104169"})[1] == 413


def test_peer_partial_unsupported_cik_and_explicit_scope(fixture):
    _, query, _, _, _, _, _, _ = fixture
    result, status = query(
        "peers", {"ciks": "0000104169,0000000999", "metrics": "operating_margin"}
    )
    assert status == 200 and any(gap.get("cik") == "0000000999" for gap in result["coverage_gaps"])
    assert query("peers", {"ciks": "0000104169"})[1] == 422


def test_certification_cannot_backcast_current_proof_or_cross_member(fixture):
    _, query, _, store, _, _, _, _ = fixture
    result, status = query("certification", {"cik": "0000104169", "as_of": "2019-12-04T00:00:00Z"})
    assert status == 200 and any(
        r["status"] == "INDEPENDENTLY_RECONCILED" for r in result["observations"]
    )
    result, status = query("certification", {"cik": "0000104169", "as_of": "2019-12-05T00:00:00Z"})
    assert status == 200 and all(
        "INDEPENDENTLY_RECONCILED" not in r["verified_statuses"] for r in result["observations"]
    )


def test_owner_market_worker_guard_precedes_dispatch_and_storage(monkeypatch):
    class Response:
        def __init__(self, body, status=200, headers=None):
            self.value, self.status = json.loads(body), status

    monkeypatch.setitem(
        sys.modules, "workers", SimpleNamespace(Response=Response, WorkerEntrypoint=object)
    )
    spec = importlib.util.spec_from_file_location(
        "research_guard_worker",
        Path(__file__).parents[2] / "deploy/cloudflare-query/src/thin_worker.py",
    )
    worker_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker_module)
    instance = worker_module.Default()
    instance.env = SimpleNamespace(
        OWNER_ONLY_MARKET_DATA="true", ZION_PRIVATE_API_TOKEN="synthetic-secret"
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Data must not be read")

    monkeypatch.setattr(worker_module, "dispatch_data_api", forbidden)
    request = SimpleNamespace(url="https://test/v1/market/prices", method="GET", headers={})
    response = asyncio.run(instance.fetch(request))
    assert response.status == 401 and response.value["error"]["code"] == "UNAUTHORIZED"


def test_response_budget_and_signed_price_cursor(fixture, monkeypatch):
    api, query, _, _, _, _, _, _ = fixture
    result, status = query("prices", {"symbol": "WMT", "limit": "0"})
    assert status == 422
    result, status = query("prices", {"symbol": "WMT", "cursor": "tampered"})
    assert status == 422
    monkeypatch.setattr(api, "MAX_RESPONSE_BYTES", 20)
    assert query("overview", {"cik": "0000104169"})[1] == 413


def test_all_supported_core_metrics_are_valid_overview_and_certification(fixture):
    _, query, _, _, _, _, _, _ = fixture
    for action in ("overview", "certification"):
        result, status = query(
            action, {"cik": "0000104169", "metrics": "cash,total_debt,diluted_shares"}
        )
        assert status == 200, result
        assert all(
            r["metric"] in ("cash", "total_debt", "diluted_shares") for r in result["observations"]
        )


def test_screen_requires_bounded_scope_and_rejects_bad_threshold_before_io(fixture):
    _, query, _, _, gets, _, _, _ = fixture
    assert query("screen", {"fundamental_minimum": "NaN"})[1] == 422
    assert not gets
    assert query("screen", {"ciks": "wrong"})[1] == 422
    assert not gets
    result, status = query("screen")
    assert status == 422 and result["error"]["code"] == "EXPLICIT_SCREEN_CIKS_REQUIRED"
    gets.clear()
    result, status = query("screen", {"ciks": "0000104169,0000078003"})
    assert status == 200
    assert len(gets) == len(set(gets))
    assert result["limits"]["source_objects_read"] == len(gets)
    assert result["limits"]["request_local_cache_hits"] >= 1


def test_screen_and_price_pages_only_include_selected_lineage(fixture):
    _, query, _, _, _, _, _, _ = fixture
    result, status = query("screen", {"ciks": "0000104169,0000078003", "limit": "1"})
    assert status == 200
    ids = {
        sid
        for row in result["matches"]
        for part in (row["fundamental"], row["price"])
        for sid in part["source_ids"]
    }
    assert set(result["source_records"]) == ids
    result, status = query("prices", {"symbol": "WMT", "limit": "1"})
    assert status == 200
    assert set(result["source_records"]) == {
        sid for row in result["prices"] for sid in row["source_ids"]
    }
    assert set(result["provenance"]["source_pages"]) == {
        r["source_page_sha256"] for r in result["source_records"].values()
    }


def test_symbol_overview_requires_exact_supported_identity_point(fixture):
    _, query, _, _, _, _, _, _ = fixture
    assert query("overview", {"symbol": "WMT"})[1] == 422
    result, status = query("overview", {"symbol": "WMT", "date_to": "2019-12-04"})
    assert status == 200 and result["identity"]["cik"] == "0000104169"
    assert result["as_of"] == "2019-12-04T00:00:00Z"
    assert query("overview", {"symbol": "WMT", "date_to": "2019-12-05"})[1] == 422


def test_valuation_flow_periods_do_not_sort_instant_balance_rows():
    api = module()

    def row(metric, value, start):
        return dict(
            metric=metric,
            value_decimal=value,
            period_start=start,
            period_end="2018-12-31",
            frequency="annual",
            unit="USD",
            currency="USD",
            available_at="2019-02-02T05:00:00Z",
            source_observation_ids=[metric],
        )

    h = {
        "observations": [
            row("cash", "1", None),
            row("revenue", "10", "2018-01-01"),
            row("operating_income", "2", "2018-01-01"),
        ]
    }
    result = api.formula_rows(h, ["operating_margin"], "2019-11-01T00:00:00Z", "0000001800")
    assert len(result) == 1
    assert result[0]["value_numerator"] == "1"
    assert result[0]["value_denominator"] == "5"
