// Report renderers: plain text for terminals and agents, a self-contained HTML page for people.
import type { Finding, Report, Severity } from "./engine.ts";

const RANK: Record<Severity, number> = { error: 3, warning: 2, info: 1 };

const p90 = (r: Report) => (r.humanP90 ? ` (human p90 ${r.humanP90})` : "");

export function toText(reports: Report[]): string {
  const out: string[] = [];
  for (const r of reports) {
    out.push(`${r.file}  [${r.lang}, ${r.words} words]  score ${r.score}${p90(r)}: ${r.verdict}  |  error ${r.counts.error}, warning ${r.counts.warning}, info ${r.counts.info}`);
    for (const d of r.docFindings) out.push(`  doc   ${d.severity.padEnd(7)} ${d.rule.padEnd(20)} ${d.message}`);
    for (const f of r.findings) {
      out.push(`  ${f.line}:${f.col}  ${f.severity.padEnd(7)} ${f.rule.padEnd(20)} "${f.text.replace(/\n/g, "⏎").slice(0, 60)}"  ${f.message}`);
    }
  }
  return out.join("\n");
}

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ESCAPES[c]!);

const spans = (f: Finding): [number, number][] => (f.parts?.length ? f.parts : [[f.start, f.end]]);

// The text with every highlighted span wrapped in <mark>. Overlapping findings split the text at every span
// edge; each piece takes the colour of its most severe finding and lists all of them in the tooltip.
function renderDoc(text: string, findings: Finding[]): string {
  const cuts = new Set<number>([0, text.length]);
  for (const f of findings) for (const [a, b] of spans(f)) cuts.add(a).add(b);
  const pts = [...cuts].sort((a, b) => a - b);
  let html = "";
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i]!, b = pts[i + 1]!;
    const chunk = esc(text.slice(a, b));
    const active = findings.filter((f) => spans(f).some(([x, y]) => x <= a && y >= b));
    if (!active.length) {
      html += chunk;
      continue;
    }
    const top = active.reduce((m, f) => (RANK[f.severity] > RANK[m.severity] ? f : m));
    const kinds = [...new Set(active.map((f) => f.kind))].join(" ");
    const fmtOnly = active.every((f) => f.kind === "markup" || f.kind === "typography");
    const tip = active.map((f) => `${f.severity}: ${f.rule}: ${f.message}`).join("\n");
    html += `<mark class="${top.severity} ${kinds}${fmtOnly ? " fmt" : ""}" title="${esc(tip)}">${chunk}</mark>`;
  }
  return html;
}

function renderSection(r: Report, text: string): string {
  const dev = r.deviations.filter((d) => d.x >= 1.5).slice(0, 8)
    .map((d) => `<span class="dev">${esc(d.rule)} ×${d.x.toFixed(1)}</span>`).join(" ");
  const style = r.docFindings.map((d) => `<span class="dev">${esc(d.message)}</span>`).join(" ");
  return `<section class="doc">
  <header><b>${esc(r.file)}</b> <span class="meta">${r.lang}, ${r.words} words, score ${r.score}${p90(r)}: <b>${r.verdict}</b>, findings per 100 words: ${r.per100.toFixed(1)}</span>
  <span class="pill error">${r.counts.error}</span><span class="pill warning">${r.counts.warning}</span><span class="pill info">${r.counts.info}</span></header>
  ${dev ? `<div class="devs">deviations from the human norm: ${dev}</div>` : ""}
  ${style ? `<div class="devs">document style: ${style}</div>` : ""}
  <div class="text">${renderDoc(text, r.findings)}</div>
</section>`;
}

export function toHtml(reports: { report: Report; text: string }[], title: string): string {
  const all = reports.flatMap((r) => r.report.findings);
  // Most frequent findings: lexical ones by unit, the others by rule.
  const units = new Map<string, { n: number; x: number; sev: Severity }>();
  for (const f of all) {
    const k = f.kind === "lexical" ? String(f.evidence.unit) : f.rule;
    const u = units.get(k) ?? { n: 0, x: Number(f.evidence.x ?? 0), sev: f.severity };
    u.n++;
    units.set(k, u);
  }
  const top = [...units.entries()].sort((a, b) => b[1].n - a[1].n).slice(0, 60);
  const words = reports.reduce((s, r) => s + r.report.words, 0);
  const cnt = (s: Severity) => all.filter((f) => f.severity === s).length;
  const docs = reports.map(({ report, text }) => renderSection(report, text)).join("\n");

  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)}</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--card:#fff;--line:#e4e1d9;--err:#f6b4ac;--warn:#f7dc8f;--info:#cfe3f7}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#17171a;--fg:#e8e6e1;--muted:#9a978f;--card:#212125;--line:#33333a;--err:#7a2f28;--warn:#6b5a1d;--info:#24425f}}
body{background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,system-ui,sans-serif;margin:0;padding:24px 16px}
main{max-width:920px;margin:0 auto}h1{font-size:22px;margin:0 0 6px}.meta,.sub{color:var(--muted);font-size:13px}
.doc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}
.doc header{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;margin-bottom:8px}
.text{white-space:pre-wrap;word-wrap:break-word}
mark{border-radius:3px;padding:0 1px;color:inherit;cursor:help}mark.error{background:var(--err)}mark.warning{background:var(--warn)}mark.info{background:var(--info)}
.pill{font-size:12px;border-radius:9px;padding:0 7px}.pill.error{background:var(--err)}.pill.warning{background:var(--warn)}.pill.info{background:var(--info)}
.devs{font-size:12px;color:var(--muted);margin-bottom:8px}.dev{display:inline-block;border:1px solid var(--line);border-radius:6px;padding:0 5px;margin:1px}
.top{display:flex;flex-wrap:wrap;gap:4px;margin:10px 0}.top span{font-size:13px;border-radius:6px;padding:1px 6px}
.top .error{background:var(--err)}.top .warning{background:var(--warn)}.top .info{background:var(--info)}
mark.fmt{background:none;outline:2px solid;outline-offset:0;font-weight:600}mark.fmt.error{outline-color:#d9534f}mark.fmt.warning{outline-color:#d4a017}mark.fmt.info{outline-color:#5b9bd5}
.legend span{display:inline-block;margin-right:10px;font-size:12px}
label{font-size:13px;margin-right:12px}body.noinfo mark.info{background:none;cursor:auto}body.nolex mark.lexical{background:none}
</style></head><body><main>
<h1>${esc(title)}</h1>
<div class="sub">${reports.length} texts, ${words} words. Errors ${cnt("error")}, warnings ${cnt("warning")}, info ${cnt("info")}. Hover a highlight to see the deviation.</div>
<p><label><input type="checkbox" onchange="document.body.classList.toggle('noinfo',!this.checked)" checked> show info</label>
<label><input type="checkbox" onchange="document.body.classList.toggle('nolex',!this.checked)" checked> show lexical findings</label></p>
<div class="legend"><span><mark class="error">fill</mark> words, phrases, constructions</span><span><mark class="error fmt">**</mark> outline: markup and typography (only the marker is highlighted)</span><span>colour: <mark class="error">error</mark> <mark class="warning">warning</mark> <mark class="info">info</mark></span></div>
<div class="sub">Most frequent findings (count, times above the human norm):</div>
<div class="top">${top.map(([k, u]) => `<span class="${u.sev}">${esc(k)} ${u.n}${u.x ? ` ×${u.x >= 10 ? Math.round(u.x) : u.x.toFixed(1)}` : ""}</span>`).join("")}</div>
${docs}
</main></body></html>`;
}
