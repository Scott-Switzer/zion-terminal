from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
src = ROOT / "deploy" / "cloudflare-query" / "src"
sys.path.insert(0, str(src))
from contract_v3 import canonical_manifest, contract_sha256  # noqa: E402

out = ROOT / "contracts"
(out / "zion-tool-contract-v3.json").write_text(json.dumps(canonical_manifest(), indent=2, sort_keys=True) + "\n")
(out / "zion-tool-contract-v3.sha256").write_text(contract_sha256() + "\n")
print(contract_sha256())
