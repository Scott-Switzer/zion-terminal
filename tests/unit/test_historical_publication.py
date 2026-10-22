import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from zion_terminal.history_store import build_release, digest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
publisher = importlib.import_module("acquire_historical_sources")


def release(tmp_path):
    raw = b"provider source"
    sha = digest(raw)
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / (sha + ".bin")).write_bytes(raw)
    return build_release(
        [
            dict(
                series_id="test:rate",
                observation_date="2000-01-03",
                value_decimal="2",
                missing_reason=None,
                unit="percent",
                asset_class="rates",
                label="Test rate",
                source_id="task",
            )
        ],
        {
            "task": dict(
                source_sha256=sha,
                url="https://home.treasury.gov/test",
                retrieved_at="2026-10-19T00:00:00Z",
            )
        },
        tmp_path,
    )


@pytest.mark.parametrize("corruption", ["raw", "series", "manifest"])
def test_corruption_prevents_every_remote_write(tmp_path, monkeypatch, corruption):
    ref = release(tmp_path)
    target = (
        next((tmp_path / "raw").iterdir())
        if corruption == "raw"
        else (
            next((tmp_path / "releases" / ref["release_id"] / "series").iterdir())
            if corruption == "series"
            else tmp_path / ref["relative_manifest"]
        )
    )
    target.write_bytes(target.read_bytes() + b" ")
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError):
        publisher.publish(tmp_path, ref)
    assert not calls


def test_manifest_is_written_last_and_readbacks_use_expected_digests(tmp_path, monkeypatch):
    ref = release(tmp_path)
    stored = {}
    writes = []

    def run(args, **kwargs):
        if args[1] == "copyto":
            stored[args[3]] = Path(args[2]).read_bytes()
            writes.append(args[3])
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(stdout=stored[args[2]])

    monkeypatch.setattr("subprocess.run", run)
    result = publisher.publish(tmp_path, ref)
    assert result["objects_verified"] == 3 and writes[-1].endswith("/manifest.json")
    assert result["publication_pointer_writes"] == 0


def test_refresh_does_not_silently_replay_old_probes(tmp_path, monkeypatch):
    probes = tmp_path / "probes"
    probes.mkdir()
    (probes / "ecb-eurofxref-hist.zip").write_bytes(b"stale-probe")
    seen = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "acquire",
            "--directory",
            str(tmp_path / "store"),
            "--reuse-probes",
            str(probes),
            "--refresh",
        ],
    )
    monkeypatch.setattr(publisher, "download", lambda url: seen.append(url) or dict(raw=b"fresh"))

    def acquire(tasks, adapters, directory, **kwargs):
        fetched = kwargs["fetcher"](publisher.ecb.tasks()[0]["url"])
        assert fetched["raw"] == b"fresh"
        return dict(fetched_tasks=1, cached_tasks=0, failures=[], observations=[], sources={})

    monkeypatch.setattr(publisher, "acquire", acquire)
    publisher.main()
    assert len(seen) == 1
