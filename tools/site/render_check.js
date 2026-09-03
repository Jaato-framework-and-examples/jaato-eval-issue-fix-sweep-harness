/**
 * Build every row of site/index.html against a real data.json, headlessly.
 *
 * WHY THIS EXISTS.  The page threw `Cannot read properties of undefined
 * (reading 'lastChild')` on its first arm table and rendered nothing, because
 * `table.append(x).lastChild` assumed append returns the node.  It returns
 * undefined.  The bug survived a hand-written check whose DOM shim returned
 * `this` from append — a stand-in that disagreed with the thing it stood for,
 * which is worse than no check at all.
 *
 * So the shim below is deliberately STRICT about the handful of DOM
 * behaviours the page relies on, and every one of them is annotated with what
 * the real DOM does.  A shim that is friendlier than the browser turns a
 * passing check into a false statement.
 *
 *     node tools/site/render_check.js site/data.json
 *
 * It is not a rendering test — nothing here knows about layout, CSS or the
 * `<details>` toggle.  It answers one question: does building every issue,
 * model and arm row from this corpus throw, and does any cell come out as
 * "undefined"/"NaN"/"[object Object]"?  Both are failures the page shows the
 * reader and no unit test on collect.py can see.
 */
"use strict";

const fs = require("fs");

/* ── the shim, matching documented DOM semantics ─────────────────────── */

class El {
  constructor(tag) {
    this.tag = tag; this.className = ""; this.title = "";
    this.children = []; this._text = null;
  }
  // Real: setting textContent REPLACES all children with one text node.
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() {
    return this._text !== null ? this._text
      : this.children.map(c => c.textContent).join("");
  }
  // Real: ParentNode.append() returns undefined.  Returning `this` here is
  // exactly the lie that let a chained call ship.
  append(...kids) {
    for (const k of kids) this.children.push(typeof k === "string" ? new Text(k) : k);
  }
  addEventListener() {}
  get lastChild() { return this.children[this.children.length - 1]; }
}
class Text {
  constructor(t) { this._text = String(t); }
  get textContent() { return this._text; }
  get children() { return []; }
}

global.document = {
  createElement: t => new El(t),
  createTextNode: t => new Text(t),
  getElementById: () => new El("p"),
};

/* ── load the page's script, minus its boot block ────────────────────── */

const page = fs.readFileSync(`${__dirname}/../../site/index.html`, "utf8");
const match = page.match(/<script>([\s\S]*?)<\/script>/);
if (!match) { console.error("render_check: no <script> in site/index.html"); process.exit(1); }
// The boot block calls fetch(); everything above it is pure construction.
const source = match[1].replace(/fetch\("data\.json"[\s\S]*$/, "");
const build = new Function(source + "\nreturn { issueRow, modelSection, armTable, passRate, agreementNote };")();

/* ── every class the script attaches must be styled ──────────────────── */

/**
 * Not a layout check — nothing here can see layout, and the bug that prompted
 * it could only be seen in a browser: `.fig .v` (a value span) and `.v` (a
 * verdict dot) were the same class, so the dot's `height: .55rem` clipped
 * every figure to a sliver of its own text. Renaming both to say what they
 * are is the fix; this guards the RENAME, catching a class the script still
 * attaches after its rule was renamed away.
 */
function checkClasses(page, source) {
  const style = page.match(/<style>([\s\S]*?)<\/style>/);
  if (!style) return ["no <style> block in site/index.html"];
  // Comments FIRST. A comment explaining a class collision mentions the class
  // it removed, and scanning it re-defines that class as far as this check is
  // concerned — which is exactly how a dead class survived here once, in the
  // comment written to explain why it was dead.
  const rules = style[1].replace(/\/\*[\s\S]*?\*\//g, " ");
  const defined = new Set([...rules.matchAll(/\.([A-Za-z][\w-]*)/g)].map(m => m[1]));

  const missing = new Set();
  // Literal class arguments to $(tag, cls, txt). A concatenated class such as
  // `"state-" + arm.state` contributes the literal prefix, matched below
  // against any defined class that starts with it.
  for (const m of source.matchAll(/\$\("\w+",\s*"([^"]*)"/g)) {
    for (const token of m[1].split(/\s+/).filter(Boolean)) {
      const known = defined.has(token) ||
        [...defined].some(cls => token.endsWith("-") && cls.startsWith(token));
      if (!known) missing.add(token);
    }
  }
  return [...missing].map(c => `class "${c}" is attached by the script but has no CSS rule`);
}

/* ── walk what was built, and look for what a reader would see ───────── */

const POISON = ["undefined", "NaN", "[object Object]", "Infinity"];

function walk(node, visit) {
  visit(node);
  for (const child of node.children || []) walk(child, visit);
}

function check(dataPath) {
  const doc = JSON.parse(fs.readFileSync(dataPath, "utf8"));
  const failures = checkClasses(page, source);
  let rows = 0, expected = 0;

  for (const issue of doc.issues) {
    let tree;
    try {
      tree = build.issueRow(issue);
    } catch (err) {
      failures.push(`issue ${issue.issue}: issueRow threw ${err}`);
      continue;
    }
    expected += issue.models.reduce((n, m) => n + m.arm_detail.length, 0);
    walk(tree, node => {
      if (node.tag === "tr" && node.children.some(c => c.tag === "td")) rows++;
      const text = node._text;
      if (typeof text === "string") {
        for (const bad of POISON) {
          if (text.includes(bad)) failures.push(`issue ${issue.issue}: cell reads "${text}"`);
        }
      }
    });
  }

  // Every arm must reach the page. A tree that builds but drops rows is the
  // failure mode a "did it throw" check cannot see.
  if (rows !== expected) failures.push(`built ${rows} arm rows, corpus has ${expected}`);

  // The by-model section renders the SAME arms transposed, so it must build
  // and reach the same total. Leaving it unchecked is how the first render
  // bug shipped: a guard that covers one renderer and not its neighbour.
  const allIssues = doc.issues.map(i => i.issue);
  let mRows = 0;
  for (const model of doc.by_model || []) {
    let tree;
    try {
      tree = build.modelSection(model, allIssues);
    } catch (err) {
      failures.push(`model ${model.profile_set}: modelSection threw ${err}`);
      continue;
    }
    walk(tree, node => {
      if (node.tag === "tr" && node.children.some(c => c.tag === "td")) mRows++;
      const text = node._text;
      if (typeof text === "string") {
        for (const bad of POISON) {
          if (text.includes(bad)) failures.push(`model ${model.profile_set}: cell reads "${text}"`);
        }
      }
    });
  }
  if (doc.by_model && mRows !== expected) {
    failures.push(`by-model built ${mRows} arm rows, corpus has ${expected}`);
  }

  if (failures.length) {
    for (const f of failures) console.error("render_check: " + f);
    return 1;
  }
  console.log(`render_check: ${doc.issues.length} issues, ${expected} arm rows (x2 views), no bad cells, every class styled`);
  return 0;
}

const path = process.argv[2] || `${__dirname}/../../site/data.json`;
if (!fs.existsSync(path)) {
  console.error(`render_check: no ${path} — run tools/site/collect.py first`);
  process.exit(1);
}
process.exit(check(path));
