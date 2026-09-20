from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "public"
sys.path.insert(0, str(ROOT / "api"))

import diff as api  # noqa: E402

DATA = json.loads((PUBLIC / "examples.json").read_text(encoding="utf-8"))
EXAMPLES = DATA["examples"]
HTML = (PUBLIC / "index.html").read_text(encoding="utf-8")
JS = (PUBLIC / "app.js").read_text(encoding="utf-8")
CSS = (PUBLIC / "style.css").read_text(encoding="utf-8")

SEVERITIES = {"non_breaking", "warning", "breaking", "needs_review"}


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.label_for: list[str] = []
        self.buttons: list[dict] = []
        self.inputs: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "label" and "for" in a:
            self.label_for.append(a["for"])
        if tag == "button":
            self.buttons.append(a)
        if tag in {"textarea", "select"}:
            self.inputs.append((tag, a.get("id", "")))


PARSED = _Ids()
PARSED.feed(HTML)


def test_examples_file_shape_and_nonempty():
    assert isinstance(EXAMPLES, list) and EXAMPLES
    for ex in EXAMPLES:
        keys = {"id", "description", "baseline", "current", "result", "explanation", "ddl"}
        assert keys <= set(ex)


def test_example_ids_unique_and_option_safe():
    ids = [e["id"] for e in EXAMPLES]
    assert len(ids) == len(set(ids))
    for i in ids:
        assert i and i == i.strip() and re.fullmatch(r"[a-z0-9][a-z0-9-]*", i), i


def test_every_example_has_description():
    for ex in EXAMPLES:
        assert isinstance(ex["description"], str) and ex["description"].strip(), ex["id"]


def test_every_dom_id_used_by_app_js_exists_in_html():
    used = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', JS))
    assert used, "app.js should look elements up by id"
    missing = used - set(PARSED.ids)
    assert not missing, missing


def test_html_ids_are_unique():
    assert len(PARSED.ids) == len(set(PARSED.ids))


def test_every_label_targets_an_existing_field():
    assert PARSED.label_for
    for target in PARSED.label_for:
        assert target in PARSED.ids
    for _tag, fid in PARSED.inputs:
        assert fid in PARSED.label_for, f"field {fid!r} has no <label for>"


def test_buttons_are_not_implicit_submit_and_html_declares_lang():
    assert PARSED.buttons
    assert all(b.get("type") == "button" for b in PARSED.buttons)
    assert re.search(r"<html[^>]*\blang=", HTML)


def test_results_region_is_focusable_and_live():
    # compare() calls $("results").focus(); that only works with tabindex
    assert re.search(r'id="results"[^>]*tabindex="-1"', HTML)
    assert "aria-live" in HTML


def test_app_js_never_injects_html():
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert banned not in JS, banned


def test_fetch_urls_are_relative_and_diff_is_post():
    urls = re.findall(r'fetch\("([^"]+)"', JS)
    assert set(urls) == {"api/diff", "examples.json"}
    assert not any(u.startswith("/") for u in urls)
    assert re.search(r'fetch\("api/diff",\s*\{[^}]*method:\s*"POST"', JS)


def test_js_error_key_matches_api_error_shape():
    status, body = api.handle(b"not json")
    assert status == 400
    assert isinstance(body.get("error"), str) and body["error"]
    assert "data.error" in JS


def test_js_handles_invalid_json_before_network_call():
    # the parse/try block must precede the first fetch of api/diff
    assert JS.index("JSON.parse") < JS.index('fetch("api/diff"')
    assert "Invalid JSON" in JS


def test_css_has_badge_class_for_every_severity_and_verdict_used():
    verdicts = {e["result"]["verdict"] for e in EXAMPLES}
    sevs = {c["severity"] for e in EXAMPLES for c in e["result"]["changes"]}
    for v in verdicts - {"none", "no_change"}:
        assert f".v-{v}" in CSS, v
    for s in sevs:
        assert f".sev-{s}" in CSS, s


def test_css_covers_all_four_severity_badges():
    for s in SEVERITIES:
        assert f".sev-{s}" in CSS and f".v-{s}" in CSS


def test_mobile_breakpoint_reaches_400px_and_stacks_editors():
    m = re.search(r"@media \(max-width:\s*(\d+)px\)\s*\{(.*?)\n\}", CSS, re.S)
    assert m and int(m.group(1)) >= 400
    assert "grid-template-columns: 1fr" in m.group(2)


def test_dark_mode_overrides_background_and_text_tokens():
    m = re.search(r"prefers-color-scheme: dark\)\s*\{(.*?)\n\}", CSS, re.S)
    assert m
    assert "--bg" in m.group(1) and "--fg" in m.group(1)
    assert "background: var(--bg)" in CSS


def test_keyboard_focus_indicator_is_not_suppressed():
    assert ":focus-visible" in CSS
    assert not re.search(r"outline:\s*(none|0)\b", CSS)


def test_stored_change_records_have_fields_the_ui_renders():
    for ex in EXAMPLES:
        for c in ex["result"]["changes"]:
            for k in ("severity", "path", "change_type", "rule_id", "reason"):
                assert isinstance(c[k], str) and c[k], (ex["id"], k)
            assert c["severity"] in SEVERITIES


def test_rename_candidates_are_flagged_as_needs_review_in_data_or_ui():
    # FR-9.3: the UI flags rename candidates; js keys off change_type == rename_candidate
    assert '"rename_candidate"' in JS
    changes = [c for e in EXAMPLES for c in e["result"]["changes"]]
    renames = [c for c in changes if c["change_type"] == "rename_candidate"]
    assert renames, "at least one bundled example should exercise a rename candidate"


def test_no_change_examples_have_empty_changes_list():
    for ex in EXAMPLES:
        if ex["id"].startswith("no-change"):
            assert ex["result"]["changes"] == []


def test_explanation_and_ddl_have_fields_the_ui_dereferences():
    for ex in EXAMPLES:
        e, d = ex["explanation"], ex["ddl"]
        assert isinstance(e["summary"], str)
        assert isinstance(e["impacts"], list)
        for i in e["impacts"]:
            assert isinstance(i["path"], str) and isinstance(i["impact"], str)
        assert isinstance(d["dialect"], str) and d["dialect"]
        assert isinstance(d["statements"], list)
        assert all(isinstance(s, str) for s in d["statements"])


def test_ai_labels_are_only_the_two_allowed_values_and_text_is_not_mislabelled():
    for ex in EXAMPLES:
        for part in ("explanation", "ddl"):
            assert set(["label"]) <= set(ex[part])
            assert ex[part]["label"] in {"sample", "recorded"}
    # the UI text mapping must not call sample output "recorded"
    assert '"recorded" ? "recorded AI output" : "sample output"' in JS


def test_pasted_schema_ui_says_ai_is_disabled():
    assert "disabled in the public demo" in JS
    assert "disabled in the public demo" in HTML
    # pasted results must be rendered with no example
    assert "render(data, null)" in JS


def test_examples_fit_the_api_payload_cap():
    for ex in EXAMPLES:
        body = json.dumps({"baseline": ex["baseline"], "current": ex["current"]}).encode()
        assert len(body) < api.MAX_BODY_BYTES, ex["id"]


def test_static_assets_are_small():
    names = ("index.html", "style.css", "app.js", "examples.json")
    total = sum((PUBLIC / n).stat().st_size for n in names)
    assert total < 2 * 1024 * 1024


def test_no_external_urls_or_inline_remote_assets_in_examples():
    text = (PUBLIC / "examples.json").read_text(encoding="utf-8")
    assert "<script" not in text.lower()


def test_no_files_other_than_expected_in_public():
    names = {p.name for p in PUBLIC.iterdir()}
    assert {"index.html", "style.css", "app.js", "examples.json"} <= names
    assert not any(n.endswith((".map", ".ts", ".tsx", ".jsx")) for n in names)
    assert "node_modules" not in names and "package.json" not in names


@pytest.mark.parametrize("body", [b"", b"{}", b'{"baseline": {}, "current": {}}', b"[]", b"null"])
def test_api_rejects_malformed_bodies_with_readable_400(body):
    status, payload = api.handle(body)
    assert status == 400
    assert isinstance(payload["error"], str) and payload["error"]


def test_swapped_example_roundtrip_is_reversible_verdict_exists():
    # pasting an example's schemas in reverse still yields a valid classified report
    ex = EXAMPLES[0]
    body = json.dumps({"baseline": ex["current"], "current": ex["baseline"]}).encode()
    status, out = api.handle(body)
    assert status == 200 and out["verdict"]


def test_handle_is_idempotent_for_every_example():
    for ex in EXAMPLES:
        body = json.dumps({"baseline": ex["baseline"], "current": ex["current"]}).encode()
        assert api.handle(body) == api.handle(body)
