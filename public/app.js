"use strict";

const $ = (id) => document.getElementById(id);
let examples = [];
let exampleShown = null; // example whose stored result is on screen, else null

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  for (const kid of kids) node.append(kid);
  return node;
}

function badge(kind, value) {
  return el("span", { class: `badge ${kind}-${value}` }, value.replace(/_/g, " "));
}

function render(report, example) {
  $("status").textContent = "";
  const verdict = $("verdict");
  verdict.replaceChildren(el("p", {}, "Overall verdict: ", badge("v", report.verdict)));
  const list = $("changes");
  list.replaceChildren();
  if (!report.changes.length) list.append(el("li", {}, "No schema changes detected."));
  for (const c of report.changes) {
    const li = el("li", {}, badge("sev", c.severity), el("strong", {}, c.path));
    li.append(` (${c.change_type.replace(/_/g, " ")}) `, el("span", { class: "rule" }, c.rule_id));
    if (c.change_type === "rename_candidate" || c.severity === "needs_review") {
      li.append(" ", el("em", {}, "needs review"));
    }
    li.append(el("div", {}, c.reason));
    list.append(li);
  }
  renderAi(example);
}

function renderAi(example) {
  const box = $("ai");
  box.replaceChildren();
  if (!example) {
    box.append(el("p", { class: "muted" },
      "AI stages (explanation, migration DDL) are disabled in the public demo for pasted schemas."));
    return;
  }
  const ex = example.explanation;
  const tag = (label) => (label === "recorded" ? "recorded AI output" : "sample output");
  const p1 = el("div", { class: "panel" }, el("h3", {}, `Explanation (${tag(ex.label)})`),
    el("p", {}, ex.summary));
  if (ex.impacts.length) {
    const ul = el("ul");
    for (const i of ex.impacts) ul.append(el("li", {}, `${i.path}: ${i.impact}`));
    p1.append(ul);
  }
  const d = example.ddl;
  const p2 = el("div", { class: "panel" },
    el("h3", {}, `Migration DDL, ${d.dialect} (${tag(d.label)})`),
    el("pre", {}, d.statements.length ? d.statements.join("\n") : "-- no statements"));
  box.append(p1, p2);
}

function fillExample(id) {
  const ex = examples.find((e) => e.id === id);
  if (!ex) return;
  $("baseline").value = JSON.stringify(ex.baseline, null, 2);
  $("current").value = JSON.stringify(ex.current, null, 2);
  exampleShown = ex;
  render(ex.result, ex);
}

async function compare() {
  let body;
  try {
    body = JSON.stringify({
      baseline: JSON.parse($("baseline").value),
      current: JSON.parse($("current").value),
    });
  } catch (e) {
    $("status").textContent = "Invalid JSON in one of the editors.";
    return;
  }
  $("status").textContent = "Comparing…";
  try {
    const res = await fetch("api/diff", { method: "POST", body });
    const data = await res.json();
    if (!res.ok) {
      $("status").textContent = data.error || "Request failed.";
      return;
    }
    exampleShown = null;
    render(data, null);
    $("results").focus();
  } catch (e) {
    $("status").textContent = "Could not reach the diff function.";
  }
}

async function init() {
  const select = $("example");
  try {
    examples = (await (await fetch("examples.json")).json()).examples;
  } catch (e) {
    select.replaceChildren(el("option", { value: "" }, "Examples unavailable"));
    return;
  }
  select.replaceChildren(el("option", { value: "" }, "Choose an example…"));
  for (const ex of examples) select.append(el("option", { value: ex.id }, `${ex.id}: ${ex.description}`));
  select.addEventListener("change", () => fillExample(select.value));
  $("run").addEventListener("click", compare);
}

init();
