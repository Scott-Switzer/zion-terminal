from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
src = ROOT / "deploy" / "cloudflare-query" / "src"
sys.path.insert(0, str(src))
from contract_v2 import canonical_manifest, contract_sha256  # noqa: E402

out = ROOT / "contracts"
out.mkdir(exist_ok=True)
(out / "zion-tool-contract-v2.json").write_bytes(json.dumps(canonical_manifest(), sort_keys=True, indent=2).encode() + b"\n")
(out / "zion-tool-contract-v2.sha256").write_text(contract_sha256() + "\n")
print(contract_sha256())
