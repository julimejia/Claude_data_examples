import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_demo_size_edge", ROOT / "scripts" / "check_demo_size.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, n: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * n)


def test_bundle_size_empty_root_is_zero(tmp_path):
    assert _load().bundle_size(tmp_path) == 0


def test_bundle_size_counts_dirs_and_files(tmp_path):
    _write(tmp_path / "api" / "a.py", 10)
    _write(tmp_path / "src" / "pkg" / "b.py", 20)
    _write(tmp_path / "public" / "i.html", 30)
    _write(tmp_path / "requirements.txt", 5)
    _write(tmp_path / "vercel.json", 7)
    assert _load().bundle_size(tmp_path) == 72


def test_bundle_size_ignores_pycache_and_pyc(tmp_path):
    _write(tmp_path / "src" / "a.py", 10)
    _write(tmp_path / "src" / "__pycache__" / "a.cpython-311.pyc", 1000)
    _write(tmp_path / "src" / "stray.pyc", 1000)
    assert _load().bundle_size(tmp_path) == 10


def test_bundle_size_ignores_paths_outside_bundle(tmp_path):
    _write(tmp_path / "src" / "a.py", 10)
    _write(tmp_path / "tests" / "big.bin", 5000)
    _write(tmp_path / ".venv" / "big.bin", 5000)
    assert _load().bundle_size(tmp_path) == 10


def test_limit_boundary_exactly_at_limit_passes(tmp_path):
    _write(tmp_path / "public" / "f", 1024 * 1024)
    assert _load().main(["--root", str(tmp_path), "--limit-mb", "1"]) == 0


def test_limit_boundary_one_byte_over_fails(tmp_path, capsys):
    _write(tmp_path / "public" / "f", 1024 * 1024 + 1)
    assert _load().main(["--root", str(tmp_path), "--limit-mb", "1"]) == 1
    assert "FAIL" in capsys.readouterr().err


def test_empty_root_passes(tmp_path, capsys):
    assert _load().main(["--root", str(tmp_path)]) == 0
    assert "OK" in capsys.readouterr().out


def test_default_limit_is_50_mb():
    assert _load().DEFAULT_LIMIT_MB == 50


def test_invalid_limit_arg_exits_nonzero(tmp_path):
    with pytest.raises(SystemExit) as e:
        _load().main(["--root", str(tmp_path), "--limit-mb", "abc"])
    assert e.value.code != 0


def test_real_bundle_far_below_limit():
    mod = _load()
    assert 0 < mod.bundle_size() < 5 * 1024 * 1024


def test_vercel_json_routes_and_function_exist():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert (ROOT / cfg["outputDirectory"]).is_dir()
    for fn in cfg["functions"]:
        assert (ROOT / fn).is_file()
    routes = cfg["routes"]
    # filesystem must come before the API route so static files win
    assert routes[0] == {"handle": "filesystem"}
    dests = [r["dest"] for r in routes if "dest" in r]
    for d in dests:
        assert (ROOT / d.lstrip("/")).is_file()
    assert any(re.match(r.get("src", ""), "/api/diff") for r in routes if "src" in r)


def test_vercel_json_function_duration_positive_and_excludes_bytecode():
    fn = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["functions"][
        "api/diff.py"
    ]
    assert fn["maxDuration"] > 0
    assert "pycache" in fn["excludeFiles"]


def test_requirements_no_heavy_deps():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for bad in ("duckdb", "deltalake", "anthropic", "openai", "sqlglot", "pytest"):
        assert bad not in text
    assert [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")] != []


def test_docs_cover_required_steps():
    doc = (ROOT / "docs" / "deploy-vercel.md").read_text(encoding="utf-8")
    low = doc.lower()
    for needle in ("import", "production branch", "vercel.json", "check_demo_size", "/api/diff"):
        assert needle in low
    # size limit documented matches the script default
    assert "50 MB" in doc


def test_docs_payload_limit_matches_api():
    doc = (ROOT / "docs" / "deploy-vercel.md").read_text(encoding="utf-8")
    api = (ROOT / "api" / "diff.py").read_text(encoding="utf-8")
    m = re.search(r"MAX_BODY_BYTES = (\d+) \* 1024", api)
    assert m and f"{m.group(1)} KB" in doc


def test_docs_referenced_static_files_exist():
    doc = (ROOT / "docs" / "deploy-vercel.md").read_text(encoding="utf-8")
    for name in ("app.js", "style.css", "examples.json"):
        assert name in doc
        assert (ROOT / "public" / name).is_file()


def test_readme_links_live_demo_placeholder_and_doc_exists():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"https://\S*vercel\.app", readme)
    assert "live demo" in readme.lower()
    assert (ROOT / "docs" / "deploy-vercel.md").is_file()
