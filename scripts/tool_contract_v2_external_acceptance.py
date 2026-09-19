from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

BASE = os.environ.get("ZION_BASE_URL", "https://api-thin-staging.scotttunnel.xyz").rstrip("/")
WORLD = {"world_type": "real", "world_id": "us-public-markets"}
EXPECTED_RELEASE = os.environ.get("EXPECTED_SERVING_RELEASE", "895eb69371fa2e944eed20463f7f9fc2")
EXPECTED_CONTRACT = "763e01d48975d02532224de73d668952bc33a199021654b5653b6c6bff9235f8"
TOOLS = {
    "resolve_entity": {"symbol": "MSFT"},
    "resolve_security": {"symbol": "BRK.B"},
    "get_fundamentals": {"symbol": "MSFT", "metrics": ["revenue"], "period": "annual", "lookback": 1},
    "get_price": {"symbol": "NVDA"},
    "get_price_history": {"symbol": "TSLA", "limit": 3},
    "get_corporate_actions": {"symbol": "NVDA", "action_types": ["STOCK_SPLIT"]},
    "get_revision_history": {"symbol": "MSFT", "metric": "revenue", "period_type": "annual"},
    "get_evidence": {},
    "compare": {"entities": ["MSFT", "ORCL", "CRM"], "metric": "operating_margin", "period": "annual", "lookback": 1},
    "calculate": {"operation": "sum", "values": ["0.1", "0.2"]},
    "screen": {"filters": [{"field": "revenue", "operator": "gt", "value": "0"}], "limit": 2},
}


def post(path: str, body: dict) -> tuple[int, dict, dict[str, str]]:
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST", headers={"content-type": "application/json", "accept": "application/json, text/event-stream", "user-agent": "zion-tool-contract-v2-acceptance/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read()), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read()), dict(error.headers)


def clean(value):
    value = deepcopy(value)
    if isinstance(value, dict):
        value.pop("request_id", None)
        value.pop("serving_telemetry", None)
        for key in list(value):
            value[key] = clean(value[key])
    elif isinstance(value, list):
        value[:] = [clean(child) for child in value]
    return value


def main() -> int:
    get_request = urllib.request.Request(BASE + "/v2/capabilities", headers={"user-agent": "zion-tool-contract-v2-acceptance/1"})
    try:
        with urllib.request.urlopen(get_request, timeout=60) as response:
            capabilities = json.loads(response.read())
            status = response.status
            headers = dict(response.headers)
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        raise SystemExit(json.dumps({"url": get_request.full_url, "status": error.code, "cf_ray": error.headers.get("cf-ray"), "body": body[:2000]}, sort_keys=True)) from error
    names = {tool["name"] for tool in capabilities.get("tools", [])}
    expected_names = set(TOOLS)
    if status != 200 or names != expected_names or capabilities.get("tool_contract_sha256") != EXPECTED_CONTRACT:
        raise SystemExit(f"capability mismatch: status={status} names={sorted(names)} hash={capabilities.get('tool_contract_sha256')}")
    if capabilities.get("serving_release_id") != EXPECTED_RELEASE:
        raise SystemExit(f"release mismatch: {capabilities.get('serving_release_id')}")

    rest, mcp = {}, {}
    evidence_id = None
    for name, arguments in TOOLS.items():
        if name == "get_evidence":
            continue
        args = dict(arguments)
        status, result, response_headers = post(f"/v2/tools/{name}", {"world": WORLD, "arguments": args})
        if status != 200 or "error" in result:
            raise SystemExit(f"REST {name} failed: {status} {result}")
        rest[name] = result
        if name == "get_fundamentals":
            evidence_id = result["data"]["observations"][0]["evidence_id"]
    if not evidence_id:
        raise SystemExit("fundamentals response did not contain evidence_id")
    status, result, _ = post("/v2/tools/get_evidence", {"world": WORLD, "arguments": {"evidence_id": evidence_id}})
    if status != 200 or "error" in result:
        raise SystemExit(f"REST get_evidence failed: {status} {result}")
    rest["get_evidence"] = result

    for name, arguments in TOOLS.items():
        call_args = dict(arguments)
        if name == "get_evidence":
            call_args = {"evidence_id": evidence_id}
        status, envelope, _ = post("/mcp", {"jsonrpc": "2.0", "id": name, "method": "tools/call", "params": {"name": name, "arguments": {**call_args, "world": WORLD}}})
        result = envelope.get("result", {}).get("structuredContent")
        if status != 200 or envelope.get("error") or not result:
            raise SystemExit(f"MCP {name} failed: {status} {envelope}")
        mcp[name] = result

    mismatches = []
    for name in sorted(set(rest) | set(mcp)):
        if clean(rest.get(name)) != clean(mcp.get(name)):
            mismatches.append(name)
    if mismatches:
        raise SystemExit(f"REST/MCP semantic mismatches: {mismatches}")
    report = {"base": BASE, "status": "PASS", "contract": EXPECTED_CONTRACT, "release": EXPECTED_RELEASE, "rest_tools": sorted(rest), "mcp_tools": sorted(mcp), "semantic_mismatches": mismatches, "cf_ray": headers.get("cf-ray")}
    output = os.environ.get("PARITY_OUTPUT")
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(report, sort_keys=True, indent=2) + "\\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
