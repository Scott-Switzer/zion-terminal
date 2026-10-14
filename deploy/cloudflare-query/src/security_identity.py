"""Source-hashed SEC cover facts and point-only security identity; no ticker backcasts."""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from html.parser import HTMLParser
from urllib.parse import urlsplit

VERSION = "sec-cover-point-identity-v1"
MAX_SOURCE_BYTES = 24 * 1048576
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class IdentityEvidenceError(ValueError):
    pass


@dataclass
class _Node:
    tag: str
    attrs: dict
    start: int
    end: int = 0
    content: list = field(default_factory=list)

    def text(self):
        return "".join(item.text() if isinstance(item, _Node) else item for item in self.content)

    def descendants(self, local_name):
        for item in self.content:
            if isinstance(item, _Node):
                if item.tag.split(":")[-1] == local_name:
                    yield item
                yield from item.descendants(local_name)


class _Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.source = text
        self.lines = [0]
        self.lines.extend(match.end() for match in re.finditer("\n", text))
        self.root = _Node("root", {}, 0)
        self.stack = [self.root]
        self.nodes = []
        self.feed(text)

    def position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.position())
        self.stack[-1].content.append(node)
        self.nodes.append(node)
        if len(self.nodes) > 1_000_000:
            raise IdentityEvidenceError("SOURCE_NODE_BUDGET")
        if tag not in VOID:
            self.stack.append(node)
        else:
            node.end = node.start + len(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop().end = self.position() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack[index].end = self.position() + len(tag) + 3
                del self.stack[index:]
                break

    def handle_data(self, text):
        self.stack[-1].content.append(text)

    def quote(self, node):
        return self.source[node.start : node.end or node.start + 512][:2048]


def _iso_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError) as exc:
        raise IdentityEvidenceError("INVALID_SOURCE_DATE") from exc


def _known_at(value):
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, AttributeError) as exc:
        raise IdentityEvidenceError("SOURCE_ACCEPTANCE_TIMEZONE_UNESTABLISHED") from exc


def _fact_value(node):
    if node.attrs.get("continuedat"):
        raise IdentityEvidenceError("CONTINUED_FACT_NOT_SUPPORTED")
    if node.attrs.get("xsi:nil", "").lower() == "true":
        raise IdentityEvidenceError("SOURCE_FACT_NIL")
    return " ".join(node.text().split())


def _shares(node, unit):
    if unit is None or unit.lower().split(":")[-1] != "shares":
        raise IdentityEvidenceError("SHARES_UNIT_NOT_SHARES")
    transform = node.attrs.get("format", "").split(":")[-1].lower()
    if transform not in {"", "numdotdecimal", "num-dot-decimal"}:
        raise IdentityEvidenceError("SHARES_TRANSFORMATION_UNSUPPORTED")
    literal = _fact_value(node)
    if "," in literal and not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", literal):
        raise IdentityEvidenceError("SHARES_LEXICAL_FORMAT_UNSUPPORTED")
    value = literal.replace(",", "").replace("\xa0", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise IdentityEvidenceError("SHARES_LEXICAL_FORMAT_UNSUPPORTED")
    try:
        scale = int(node.attrs.get("scale", "0"))
        if not -12 <= scale <= 12 or node.attrs.get("sign") == "-":
            raise ValueError()
        with localcontext() as context:
            context.prec = max(80, len(value) + abs(scale) + 10)
            amount = Decimal(value) * (Decimal(10) ** scale)
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ValueError()
    except (InvalidOperation, ValueError) as exc:
        raise IdentityEvidenceError("SHARES_SCALE_OR_VALUE_INVALID") from exc
    return format(amount, "f")


def _exchange_context_matches(symbol, exchange):
    """Match the same class/period; only the explicit listing-exchange axis may differ."""
    if not exchange or exchange["typed_dimensions"]:
        return False
    symbol_exchange = {
        d.get("member")
        for d in symbol["dimensions"]
        if d.get("axis") == "dei:EntityListingsExchangeAxis"
    }
    exchange_listing = {
        d.get("member")
        for d in exchange["dimensions"]
        if d.get("axis") == "dei:EntityListingsExchangeAxis"
    }
    if symbol_exchange and symbol_exchange != exchange_listing:
        return False
    for key in ("cik", "instant", "period_start", "period_end"):
        if symbol.get(key) != exchange.get(key):
            return False

    def dimensions(context):
        return sorted(
            (d.get("axis") or "", d.get("member") or "")
            for d in context["dimensions"]
            if d.get("axis") != "dei:EntityListingsExchangeAxis"
        )

    return dimensions(symbol) == dimensions(exchange)


def filing_anchors(
    raw,
    *,
    expected_sha256,
    cik,
    accession,
    filing_date,
    source_key,
    source_url,
    metadata_sha256,
    accepted_at=None,
):
    """Return literal cover observations; valid only on the supplied source filing date.

    Caller supplies hash-verified SEC filing metadata. A source timestamp without
    timezone is never relabeled UTC. Outstanding shares keep their own context date.
    """
    if len(raw) > MAX_SOURCE_BYTES or b"<!ENTITY" in raw.upper():
        raise IdentityEvidenceError("SOURCE_SIZE_OR_ENTITY_DECLARATION")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise IdentityEvidenceError("SOURCE_HASH_MISMATCH")
    if not re.fullmatch("[0-9]{10}", cik) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise IdentityEvidenceError("SOURCE_FILING_IDENTITY")
    if not re.fullmatch("[a-f0-9]{64}", metadata_sha256):
        raise IdentityEvidenceError("SOURCE_METADATA_HASH_REQUIRED")
    parsed = urlsplit(source_url)
    expected_prefix = f"/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.sec.gov", "sec.gov"}
        or not parsed.path.startswith(expected_prefix)
    ):
        raise IdentityEvidenceError("OFFICIAL_SOURCE_URL_REQUIRED")
    observed = _iso_date(filing_date)
    known = _known_at(accepted_at)
    document = _Document(raw.decode("utf-8", errors="strict"))
    contexts = {}
    units = {}
    facts = {}
    for node in document.nodes:
        local = node.tag.split(":")[-1]
        if local == "context":
            identifiers = list(node.descendants("identifier"))
            instants = list(node.descendants("instant"))
            starts = list(node.descendants("startdate"))
            ends = list(node.descendants("enddate"))
            members = list(node.descendants("explicitmember"))
            typed = list(node.descendants("typedmember"))
            contexts[node.attrs.get("id")] = {
                "cik": identifiers[0].text().strip().zfill(10) if len(identifiers) == 1 else None,
                "instant": _iso_date(instants[0].text().strip()) if len(instants) == 1 else None,
                "period_start": _iso_date(starts[0].text().strip()) if len(starts) == 1 else None,
                "period_end": _iso_date(ends[0].text().strip()) if len(ends) == 1 else None,
                "dimensions": [
                    {"axis": member.attrs.get("dimension"), "member": member.text().strip()}
                    for member in members
                ],
                "typed_dimensions": bool(typed),
                "quote": document.quote(node),
            }
        elif local == "unit":
            measures = list(node.descendants("measure"))
            units[node.attrs.get("id")] = measures[0].text().strip() if len(measures) == 1 else None
        elif local in {"nonnumeric", "nonfraction"}:
            name = node.attrs.get("name", "")
            if name.lower().startswith("dei:"):
                facts.setdefault(name.split(":")[-1].lower(), []).append(node)
    forms = {_fact_value(node) for node in facts.get("documenttype", [])}
    if len(forms) != 1 or not forms.issubset({"10-K", "10-Q", "10-K/A", "10-Q/A"}):
        raise IdentityEvidenceError("SOURCE_FORM_NOT_SUPPORTED")
    form = next(iter(forms))
    source = {
        "source_id": "SEC",
        "rights": "PUBLIC_SEC_ONLY",
        "source_key": source_key,
        "source_url": source_url,
        "source_sha256": expected_sha256,
        "metadata_sha256": metadata_sha256,
        "cik": cik,
        "accession": accession,
        "form": form,
        "filing_date": observed,
        "known_at": known,
        "knowledge_status": "EXACT_SOURCE_ACCEPTANCE"
        if known
        else "SOURCE_DATE_ONLY_NO_INTRADAY_PIT",
    }
    identity = []
    shares = []
    unsupported = []
    for node in facts.get("tradingsymbol", []):
        context = contexts.get(node.attrs.get("contextref"))
        if not context or context["cik"] != cik or context["typed_dimensions"]:
            unsupported.append("TRADING_SYMBOL_CONTEXT_UNSUPPORTED")
            continue
        symbol = _fact_value(node)
        if not re.fullmatch("[A-Za-z0-9][A-Za-z0-9.\\-]{0,19}", symbol):
            unsupported.append("TRADING_SYMBOL_VALUE_UNSUPPORTED")
            continue
        exchanges = [
            candidate
            for candidate in facts.get("securityexchangename", [])
            if _exchange_context_matches(context, contexts.get(candidate.attrs.get("contextref")))
        ]
        names = {_fact_value(candidate) for candidate in exchanges}
        if len(names) != 1:
            unsupported.append("TRADING_SYMBOL_EXCHANGE_AMBIGUOUS_OR_MISSING")
            continue
        identity.append(
            {
                **source,
                "schema_version": VERSION,
                "symbol": symbol,
                "entity_id": "entity:sec:cik:" + cik,
                "exchange_name": next(iter(names)),
                "context_id": node.attrs.get("contextref"),
                "context_date": context["instant"],
                "dimensions": context["dimensions"],
                "observed_on": observed,
                "valid_from": observed,
                "valid_to": observed,
                "identity_status": "POINT_SUPPORTED_FILING_COVER_ONLY",
                "symbol_quote": document.quote(node),
                "exchange_quote": document.quote(exchanges[0]),
                "exchange_context_quote": contexts[exchanges[0].attrs["contextref"]]["quote"],
                "context_quote": context["quote"],
            }
        )
    for node in facts.get("entitycommonstocksharesoutstanding", []):
        context = contexts.get(node.attrs.get("contextref"))
        if (
            not context
            or context["cik"] != cik
            or not context["instant"]
            or context["typed_dimensions"]
        ):
            unsupported.append("SHARES_CONTEXT_UNSUPPORTED")
            continue
        try:
            value = _shares(node, units.get(node.attrs.get("unitref")))
        except IdentityEvidenceError as exc:
            unsupported.append(str(exc))
            continue
        shares.append(
            {
                **source,
                "concept": "dei:EntityCommonStockSharesOutstanding",
                "value_decimal": value,
                "unit": "shares",
                "shares_date": context["instant"],
                "dimensions": context["dimensions"],
                "share_scope": "CLASS_SPECIFIC"
                if context["dimensions"]
                else "UNDIMENSIONED_SOURCE_FACT",
                "context_id": node.attrs.get("contextref"),
                "source_quote": document.quote(node),
                "context_quote": context["quote"],
                "weighted_average_substitution": False,
            }
        )
    return {
        "schema_version": VERSION,
        "source": source,
        "identity_anchors": identity,
        "outstanding_shares": shares,
        "unsupported_reasons": sorted(set(unsupported)),
        "historical_interval_inference": False,
        "source_financial_certification": False,
    }


def resolve_point(anchors, symbol, on_date, *, as_of=None):
    """Only exact observed dates; no current-ticker or unbounded interval fallback."""
    on_date = _iso_date(on_date)
    cutoff = _known_at(as_of) if as_of is not None else None
    found = []
    for anchor in anchors:
        if anchor.get("identity_status") != "POINT_SUPPORTED_FILING_COVER_ONLY":
            continue
        if anchor.get("symbol") != symbol or anchor.get("observed_on") != on_date:
            continue
        if anchor.get("valid_from") != on_date or anchor.get("valid_to") != on_date:
            raise IdentityEvidenceError("POINT_INTERVAL_TAMPERED")
        if cutoff is not None and (
            not anchor.get("known_at") or _known_at(anchor["known_at"]) > cutoff
        ):
            continue
        found.append(anchor)
    identities = {
        (
            row["cik"],
            row.get("exchange_name"),
            tuple((d.get("axis"), d.get("member")) for d in row.get("dimensions", [])),
        )
        for row in found
    }
    if not identities:
        raise IdentityEvidenceError("DATED_SECURITY_IDENTITY_NOT_ESTABLISHED")
    if len(identities) > 1:
        raise IdentityEvidenceError("AMBIGUOUS_SECURITY_IDENTITY")
    return found[0]
