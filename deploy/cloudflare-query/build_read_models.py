from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path

BUCKET = "financial-system-datasets"
SEC_BUCKET = "ppe-sec-intelligence-prod"
CIKS = {"AAPL": "0000320193", "MSFT": "0000789019", "NVDA": "0001045810", "JPM": "0000019617", "TSLA": "0001318605"}
TAG_MAP = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", ":Revenues"),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold"),
    "gross_profit": ("GrossProfit",),
    "operating_income": (":OperatingIncomeLoss",),
    "net_income": (":NetIncomeLoss", "ProfitLoss"),
    "assets": (":Assets",),
    "liabilities": (":Liabilities",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "debt": ("LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtNoncurrent"),
    "shares_outstanding": ("EntityCommonStockSharesOutstanding",),
    "eps_diluted": ("EarningsPerShareDiluted",),
}


def r2(bucket: str, key: str) -> bytes:
    return subprocess.check_output(["wrangler", "r2", "object", "get", "--remote", "--pipe", f"{bucket}/{key}"])


def put(bucket: str, key: str, path: Path) -> None:
    subprocess.run(["wrangler", "r2", "object", "put", f"{bucket}/{key}", "--remote", "--file", str(path)], check=True)


def attrs(raw: str) -> dict[str, str]:
    return dict(re.findall(r'(\w[\w:-]*)\s*=\s*["\']([^"\']*)["\']', raw))


def parse_facts(html: str, filing: dict) -> list[dict]:
    contexts: dict[str, tuple[str, str, bool]] = {}
    for match in re.finditer(r'<xbrli:context\b[^>]*id=["\']([^"\']+)["\'][^>]*>(.*?)</xbrli:context>', html, re.I | re.S):
        cid, body = match.groups()
        start = re.search(r'<xbrli:startDate>([^<]+)', body, re.I)
        end = re.search(r'<xbrli:endDate>([^<]+)', body, re.I)
        instant = re.search(r'<xbrli:instant>([^<]+)', body, re.I)
        contexts[cid] = (start.group(1) if start else "", end.group(1) if end else (instant.group(1) if instant else ""), bool(re.search(r"<xbrli:segment\b|<xbrldi:", body, re.I)))
    rows = []
    for match in re.finditer(r'<ix:nonFraction\b(?P<a>[^>]*)>(?P<v>[^<]*)</ix:nonFraction>', html, re.I | re.S):
        a = attrs(match.group("a")); name = a.get("name", ""); metric = next((m for m, tags in TAG_MAP.items() if name.endswith(tags)), None)
        if not metric or a.get("contextRef") not in contexts: continue
        start, end, segmented = contexts[a["contextRef"]]
        if not end or segmented: continue
        try:
            value = float(match.group("v").replace(",", "")) * 10 ** int(a.get("scale", "0"))
            if a.get("sign") == "-": value = -abs(value)
        except ValueError: continue
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days if start else 0
        period = "annual" if 300 <= days <= 380 else "quarterly" if 75 <= days <= 110 else "instant" if not start else None
        if not period: continue
        rows.append({"metric": metric, "value": value, "unit": "USD" if metric not in {"eps_diluted", "shares_outstanding"} else "USD/share" if metric == "eps_diluted" else "shares", "period": period, "period_start": start or end, "period_end": end, "filing_date": filing["filing_date"], "available_at": filing["filing_date"] + "T00:00:00Z", "form": filing["form"], "accession": filing["accession_number"], "source_record": name, "context": a.get("contextRef")})
    return rows


def latest_filings(manifest: dict, cik: str) -> list[dict]:
    paths = [a["path"] for a in manifest["artifacts"] if cik in a.get("path", "") and a["path"].endswith("/manifest.json") and "raw/sec/filings/" in a["path"]]
    return [json.loads(r2(SEC_BUCKET, f"{manifest['_prefix']}/{path}")) for path in paths]


def build_symbol(symbol: str, current: dict, corpus: dict, tmp: Path) -> dict:
    cik = CIKS[symbol]; corpus_prefix = corpus["_prefix"]
    filings = latest_filings(corpus, cik)
    rows = []
    for filing in filings:
        accession = filing["accession_number"]
        primary = next(a["path"] for a in corpus["artifacts"] if accession in a.get("path", "") and a["path"].endswith("/primary.html"))
        html = r2(SEC_BUCKET, f"{corpus_prefix}/{primary}").decode("utf-8", "replace")
        rows.extend(parse_facts(html, filing))
    # Keep one deterministic fact per metric/period end/form, preferring the latest filing.
    chosen = {}
    for row in rows: chosen[(row["metric"], row["period"], row["period_end"])] = row
    rows = list(chosen.values())
    by_key = {(r["period"], r["period_end"]): r for r in rows}
    for period_end in sorted({r["period_end"] for r in rows}):
        for period in {r["period"] for r in rows if r["period_end"] == period_end}:
            values = {r["metric"]: r for r in rows if r["period"] == period and r["period_end"] == period_end}
            revenue = values.get("revenue"); operating = values.get("operating_income"); gross = values.get("gross_profit"); net = values.get("net_income")
            for metric, numerator, formula in [("gross_margin", gross, "gross_profit / revenue"), ("operating_margin", operating, "operating_income / revenue"), ("net_margin", net, "net_income / revenue")]:
                if numerator and revenue and revenue["value"]:
                    rows.append({**numerator, "metric": metric, "value": numerator["value"] / revenue["value"], "unit": "ratio", "calculation": formula})
    price_path = tmp / f"{symbol}.parquet"
    price_key = f"{current['prefix']}/securities/{symbol}/price_history.parquet"
    price_path.write_bytes(r2(BUCKET, price_key))
    import pyarrow.parquet as pq
    prices = []
    for row in pq.read_table(price_path).to_pylist()[-500:]:
        prices.append({"session": row["date"], "open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"]), "volume": float(row["volume"]), "unit": "USD/share", "available_at": row["date"] + "T21:00:00Z", "source": "PPE-published-release", "release_id": current["prefix"]})
    return {"schema_version": "1", "symbol": symbol, "entity_id": f"real:equity:{symbol}", "base_release": current["prefix"], "observations": rows, "price_history": prices}


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--symbols", default="AAPL,MSFT,NVDA"); parser.add_argument("--publish", action="store_true"); parser.add_argument("--output", type=Path, default=Path("/tmp/zion-read-models")); args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    current = json.loads(r2(BUCKET, "control/market-terminal/CURRENT.json"))
    corpus_prefix = "sec-filing-intelligence/corpus/v1/b7fc2b52683b61ef53f527e1f766ab1a91eb86346a0f90136d57b0121173fc2b"
    corpus = json.loads(r2(SEC_BUCKET, f"{corpus_prefix}/corpus_manifest.json")); corpus["_prefix"] = corpus_prefix
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    payloads = {}
    for symbol in symbols:
        if symbol not in CIKS: raise SystemExit(f"unsupported bounded symbol: {symbol}")
        payloads[symbol] = build_symbol(symbol, current, corpus, args.output)
        (args.output / f"{symbol}.json").write_text(json.dumps(payloads[symbol], sort_keys=True, separators=(",", ":")))
    manifest = {"schema_version": "1", "base_release": current["prefix"], "corpus_manifest_sha256": hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest(), "symbols": symbols, "artifacts": {s: hashlib.sha256((args.output / f"{s}.json").read_bytes()).hexdigest() for s in symbols}}
    (args.output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    print(json.dumps(manifest, indent=2))
    if args.publish:
        release = hashlib.sha256((args.output / "manifest.json").read_bytes()).hexdigest(); prefix = f"gold/market-terminal/fundamentals/releases/{release}"
        for path in [args.output / "manifest.json", *(args.output / f"{s}.json" for s in symbols)]: put(BUCKET, f"{prefix}/{path.name}", path)
        pointer = args.output / "CURRENT.json"; pointer.write_text(json.dumps({"prefix": prefix, "release_id": release, "base_release": current["prefix"], "symbols": symbols}, sort_keys=True, separators=(",", ":")))
        put(BUCKET, "control/market-terminal/fundamentals/CURRENT.json", pointer); print(prefix)

if __name__ == "__main__": main()
