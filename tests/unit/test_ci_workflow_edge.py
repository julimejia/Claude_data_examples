import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _doc() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return [s for j in _doc()["jobs"].values() for s in j["steps"]]


def _runs() -> list[str]:
    return [s["run"] for s in _steps() if "run" in s]


def _triggers() -> dict:
    d = _doc()
    return d.get("on", d.get(True))


def test_only_push_trigger_no_pull_request_or_wildcards():
    t = _triggers()
    assert set(t) == {"push"}
    assert t["push"]["branches"] == ["dev"]
    assert "tags" not in t["push"]


def test_step_order_install_lint_tests_evals():
    runs = _runs()
    idx = {
        k: next(i for i, r in enumerate(runs) if k in r)
        for k in ("pip install", "ruff check", "pytest", "schemasentinel.evals")
    }
    assert idx["pip install"] < idx["ruff check"] < idx["pytest"] < idx["schemasentinel.evals"]


def test_checkout_precedes_everything():
    assert "checkout" in _steps()[0]["uses"]


def test_python_version_satisfies_project_minimum():
    setup = [s for s in _steps() if "setup-python" in s.get("uses", "")]
    assert setup
    v = str(setup[0]["with"]["python-version"])
    major, minor = (int(x) for x in v.split(".")[:2])
    assert (major, minor) >= (3, 11)


def test_install_includes_dev_extra_needed_for_ruff_and_pytest():
    install = next(r for r in _runs() if "pip install" in r)
    assert "dev" in install


def test_no_secrets_or_llm_credentials_and_no_record_mode():
    text = CI.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "ANTHROPIC" not in text.upper()
    assert "TELEGRAM" not in text.upper()
    assert "--record" not in text
    assert not re.search(r"--(live|llm)", text)


def test_permissions_are_read_only():
    perms = _doc().get("permissions")
    assert perms is not None
    assert all(v == "read" for v in perms.values())


def test_every_step_is_exactly_uses_or_run():
    for s in _steps():
        assert ("uses" in s) ^ ("run" in s)


def test_install_extras_exist_in_pyproject():
    install = next(r for r in _runs() if "pip install" in r)
    extras = re.search(r"\[([^\]]+)\]", install).group(1).split(",")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for e in extras:
        assert re.search(rf"^{e.strip()}\s*=", pyproject, re.M), e


def test_evals_module_is_runnable_entrypoint():
    assert (ROOT / "src" / "schemasentinel" / "evals" / "__main__.py").is_file()
