#!/usr/bin/env python3
"""Tiny local web UI for Outbound List Maker.

One page where you can:
  - paste an outbound brief
  - generate relevant people with names and public emails
  - click Build to clean/dedupe/prioritize and export the contact list

Run:
    python scripts/app.py            # serves http://127.0.0.1:8000
    PORT=8500 python scripts/app.py  # different port

AI generation needs an Anthropic API key:
    export ANTHROPIC_API_KEY=sk-ant-...
"""

import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_targets as bt  # noqa: E402  (shares the pipeline + helpers)

PORT = int(os.environ.get("PORT", "8000"))
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
CONTACTS = ROOT / "data" / "current_contacts.csv"
SEED_INPUTS = ROOT / "data" / "seed_inputs.json"


# --------------------------------------------------------------------------- #
# Data helpers
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG = "outbound_list.yaml"


def list_configs():
    names = sorted(p.name for p in (ROOT / "config").glob("*.yaml"))
    # surface the generic app config first; keep the template last
    names.sort(key=lambda n: (n == "example_template.yaml", n != DEFAULT_CONFIG, n))
    return names


def load_config(name):
    return bt.load_config(ROOT / "config" / name)


def read_rows(columns):
    if not CONTACTS.exists():
        return []
    rows, _ = bt.read_seeds(CONTACTS)
    return [{c: (r.get(c) or "").strip() for c in columns} for r in rows]


def write_rows(columns, rows):
    bt.write_csv(CONTACTS, columns, rows)


def read_seed_inputs():
    if not SEED_INPUTS.exists():
        return []
    try:
        data = json.loads(SEED_INPUTS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = data if isinstance(data, list) else []
    clean = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        clean.append({
            "name": str(row.get("name", "") or "").strip(),
            "kind": str(row.get("kind", "Company") or "Company").strip(),
            "website": str(row.get("website", "") or "").strip(),
            "notes": str(row.get("notes", "") or "").strip(),
        })
    return [r for r in clean if r["name"] or r["website"] or r["notes"]]


def write_seed_inputs(rows):
    SEED_INPUTS.parent.mkdir(parents=True, exist_ok=True)
    SEED_INPUTS.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def config_summary(name):
    cfg = load_config(name)
    return {
        "config": name,
        "configs": list_configs(),
        "columns": cfg["output_columns"],
        "target_count": cfg["target_count"],
        "companies_to_find": cfg.get("companies_to_find", 12),
        "contacts_per_company": cfg.get("contacts_per_company", 2),
        "categories": cfg["scope"]["categories"],
        "firm_types": cfg["scope"].get("firm_types", []),
        "category_targets": cfg.get("category_targets", {}),
        "geography": cfg["scope"]["geography"],
        "project_name": cfg["project_name"],
        "seed_inputs": read_seed_inputs(),
        "rows": read_rows(cfg["output_columns"]),
    }


# --------------------------------------------------------------------------- #
# AI generation
# --------------------------------------------------------------------------- #
def parse_json_array(text):
    """Extract the first complete JSON array from model text."""
    text = text.strip()
    if text.startswith("```"):
        text = text.replace("```json", "```", 1)
        text = text.strip("` \n")
    start = text.find("[")
    if start == -1:
        raise RuntimeError("AI did not return a JSON array. Got:\n" + text[:700])

    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])

    raise RuntimeError(
        "AI returned partial JSON even after batching. Restart the app if you "
        "still see the older 'AI did not return JSON' wording. Got:\n" + text[:700]
    )


def call_anthropic(prompt, max_tokens=12000):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Run: export ANTHROPIC_API_KEY=sk-ant-..."
        )

    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        payload = json.loads(resp.read())

    return "".join(
        b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
    )


def normalize_rows(items, columns):
    rows = []
    for it in items:
        row = {c: str(it.get(c, "") or "").strip() for c in columns}
        email = row.get("contact_email", "")
        if email and ("@" not in email or " " in email):
            row["contact_email"] = ""
        rows.append(row)
    return rows


def format_seed_inputs(seed_inputs):
    lines = []
    for i, seed in enumerate(seed_inputs, start=1):
        parts = [f"{i}. {seed.get('kind', 'Seed')}: {seed.get('name', '').strip()}"]
        if seed.get("website"):
            parts.append(f"website: {seed['website']}")
        if seed.get("notes"):
            parts.append(f"notes: {seed['notes']}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def build_generation_prompt(cfg, columns, brief, seed_inputs, category, count, contacts_per_company, extra=""):
    ctx = cfg["company_context"]
    extra_txt = ""
    if extra and extra.strip():
        extra_txt = "\nAdditional instructions:\n" + extra.strip()

    return f"""Return a sourced outbound people list as raw JSON.

Output exactly one JSON array. Do not use markdown. Do not wrap it in ``` fences.
Return exactly {count} people for category: {category}.

User brief:
{brief.strip()}

User-provided seeds. Use these to shape the list:
{format_seed_inputs(seed_inputs)}

Fallback context:
- Company selling: {ctx['company']}
- Product: {ctx['product_description']}
- Problem solved: {ctx['core_problem']}
- Relevant workflows: {", ".join(ctx.get('relevant_workflows', []))}

Geography: {cfg['scope']['geography']}
Allowed firm_type values:
{chr(10).join('- ' + s for s in cfg['scope'].get('firm_types', []))}
Allowed category for every row: {category}
People per company target: {contacts_per_company}

Required rules:
- Each row is one real person at one real company.
- Use the seeds as the starting point. Include relevant people from seed companies when possible, then find similar companies/people that match the pattern in the seeds.
- If a seed is a person, include that person when they match the brief and you can find a public exact email.
- If a seed is a company, find relevant people at that company with public exact emails.
- contact_name is required. Do not return blank contact_name.
- contact_email is required. Use only a public, exact email for that person.
- Never guess or synthesize email formats. If you cannot find a public exact email for a person, choose a different person.
- Use about {contacts_per_company} people per company, never more than 3.
- Prefer named relevant operators, founders, executives, department heads, or workflow owners.
- linkedin_url must be blank unless you know the real public profile URL.
- source_url must support the person, email, title, company, or relevance.
- firm_type must be exactly one of the allowed firm_type values.
- why_relevant must be one short direct sentence.
- priority must be exactly High, Medium, or Low.
- Keep values concise.
{extra_txt}

Each object must have exactly these keys:
{json.dumps(columns)}
"""


def generate_batch(cfg, columns, brief, seed_inputs, category, count, contacts_per_company, extra=""):
    if count <= 0:
        return []

    prompt = build_generation_prompt(
        cfg, columns, brief, seed_inputs, category, count, contacts_per_company, extra
    )
    try:
        text = call_anthropic(prompt, max_tokens=5000)
        items = parse_json_array(text)
        return normalize_rows(items[:count], columns)
    except Exception as e:
        # Large generations are the usual source of partial JSON. Split and retry
        # so users get rows instead of a dead-end parser error.
        if count > 2:
            left = count // 2
            right = count - left
            return (
                generate_batch(cfg, columns, brief, seed_inputs, category, left, contacts_per_company, extra)
                + generate_batch(cfg, columns, brief, seed_inputs, category, right, contacts_per_company, extra)
            )
        raise RuntimeError(
            f"AI could not produce valid JSON for {category}. "
            "Try reducing the contact count or simplifying the brief. "
            f"Last error: {e}"
        )


def generate_with_ai(name, brief, seed_inputs, company_count, contacts_per_company, per_category=None, extra=""):
    if not brief or not brief.strip():
        raise RuntimeError("Paste an outbound brief first.")
    if not seed_inputs:
        raise RuntimeError("Add at least one seed before generating.")

    cfg = load_config(name)
    columns = cfg["output_columns"]
    company_count = max(1, int(company_count or cfg.get("companies_to_find", 12)))
    contacts_per_company = max(1, min(3, int(contacts_per_company or cfg.get("contacts_per_company", 2))))
    count = company_count * contacts_per_company

    breakdown = {k: int(v) for k, v in (per_category or {}).items() if int(v) > 0}
    if not breakdown:
        cats = cfg["scope"]["categories"]
        base = count // len(cats)
        extra_rows = count % len(cats)
        breakdown = {cat: base + (1 if i < extra_rows else 0) for i, cat in enumerate(cats)}

    rows = []
    max_rows_per_call = 3
    for category, category_count in breakdown.items():
        remaining = int(category_count)
        while remaining > 0:
            batch_count = min(max_rows_per_call, remaining)
            rows.extend(
                generate_batch(
                    cfg, columns, brief, seed_inputs, category, batch_count, contacts_per_company, extra
                )
            )
            remaining -= batch_count
    return rows


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter console
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    # ---- GET -------------------------------------------------------------- #
    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            return self._send(200, PAGE, "text/html; charset=utf-8")

        if self.path.startswith("/api/state"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            name = (q.get("config") or list_configs()[:1] or [DEFAULT_CONFIG])[0]
            if name not in list_configs():
                name = list_configs()[0]
            return self._send(200, config_summary(name))

        if self.path.startswith("/download/"):
            fname = self.path.split("/download/", 1)[1]
            target = (ROOT / "data" / fname) if fname.endswith(".csv") else (ROOT / "docs" / fname)
            if not target.exists():
                return self._send(404, {"error": "not found"})
            ctype = "text/csv" if fname.endswith(".csv") else "text/markdown"
            return self._send(200, target.read_bytes(), ctype)

        return self._send(404, {"error": "not found"})

    # ---- POST ------------------------------------------------------------- #
    def do_POST(self):
        try:
            data = self._json_body()
            name = data.get("config") or list_configs()[0]
            cfg = load_config(name)
            columns = cfg["output_columns"]

            if self.path == "/api/add":
                rows = read_rows(columns)
                rows.append({c: (data.get("row", {}).get(c, "") or "").strip()
                             for c in columns})
                write_rows(columns, rows)
                return self._send(200, {"rows": rows})

            if self.path == "/api/delete":
                rows = read_rows(columns)
                i = int(data.get("index", -1))
                if 0 <= i < len(rows):
                    rows.pop(i)
                write_rows(columns, rows)
                return self._send(200, {"rows": rows})

            if self.path == "/api/clear":
                write_rows(columns, [])
                return self._send(200, {"rows": []})

            if self.path == "/api/seed/add":
                seed = data.get("seed", {}) or {}
                row = {
                    "name": (seed.get("name", "") or "").strip(),
                    "kind": (seed.get("kind", "Company") or "Company").strip(),
                    "website": (seed.get("website", "") or "").strip(),
                    "notes": (seed.get("notes", "") or "").strip(),
                }
                if not (row["name"] or row["website"] or row["notes"]):
                    raise RuntimeError("Add a seed name, website, or note.")
                seeds = read_seed_inputs()
                seeds.append(row)
                write_seed_inputs(seeds)
                return self._send(200, {"seed_inputs": seeds})

            if self.path == "/api/seed/delete":
                seeds = read_seed_inputs()
                i = int(data.get("index", -1))
                if 0 <= i < len(seeds):
                    seeds.pop(i)
                write_seed_inputs(seeds)
                return self._send(200, {"seed_inputs": seeds})

            if self.path == "/api/seed/clear":
                write_seed_inputs([])
                return self._send(200, {"seed_inputs": []})

            if self.path == "/api/generate":
                mode = data.get("mode", "append")
                per_category = data.get("per_category") or {}
                brief = data.get("brief", "")
                seed_inputs = read_seed_inputs()
                company_count = int(data.get("company_count", cfg.get("companies_to_find", 12)))
                contacts_per_company = int(data.get("contacts_per_company", cfg.get("contacts_per_company", 2)))
                extra = data.get("extra", "")
                new_rows = generate_with_ai(
                    name,
                    brief,
                    seed_inputs,
                    company_count,
                    contacts_per_company,
                    per_category,
                    extra,
                )
                rows = ([] if mode == "replace" else read_rows(columns)) + new_rows
                write_rows(columns, rows)
                return self._send(200, {"rows": rows, "generated": len(new_rows)})

            if self.path == "/api/build":
                stats = bt.build(ROOT / "config" / name)
                return self._send(200, {
                    "exported": stats["exported"],
                    "incomplete": stats["incomplete"],
                    "duplicates_removed": stats["duplicates_removed"],
                    "total_reviewed": stats["total_reviewed"],
                    "target_count": stats["target_count"],
                    "usable": stats["usable"],
                })

            return self._send(404, {"error": "unknown endpoint"})
        except Exception as e:  # surface errors to the UI
            return self._send(500, {"error": str(e)})


# --------------------------------------------------------------------------- #
# Page (single file, no build step)
# --------------------------------------------------------------------------- #
PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Outbound List Maker</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--bd:#e2e5ea;--mut:#6b7280;--bg:#f4f6f8;--ac:#2563eb;--ac2:#111827;--ok:#157347;--err:#b42318}
  *{box-sizing:border-box}
  body{font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;background:var(--bg);color:#111827}
  header{background:var(--ac2);color:#fff;padding:18px 24px}
  header .wrap{max-width:1160px;margin:0 auto}
  h1{font-size:19px;margin:0;font-weight:650;letter-spacing:.2px}
  .sub{color:#cbd5e1;font-size:13px;margin-top:3px}
  main{max-width:1160px;margin:0 auto;padding:22px 24px 60px}
  .card{background:#fff;border:1px solid var(--bd);border-radius:12px;padding:18px 20px;margin-bottom:18px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
  .card h2{font-size:12px;margin:0 0 4px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);font-weight:700}
  .card .hint{color:var(--mut);font-size:13px;margin:0 0 14px}
  label{display:block;font-size:12px;color:var(--mut);margin:0 0 4px;font-weight:600}
  input,select,textarea{width:100%;padding:8px 10px;border:1px solid var(--bd);border-radius:8px;font:inherit;background:#fff;color:#111827}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px rgba(37,99,235,.12)}
  textarea{min-height:70px;resize:vertical}
  .grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
  .row{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}
  .catgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}
  .catgrid .cat{display:flex;align-items:center;justify-content:space-between;gap:8px;border:1px solid var(--bd);border-radius:8px;padding:6px 8px 6px 12px}
  .catgrid .cat span{font-size:13px;color:#374151}
  .catgrid .cat input{width:64px;text-align:center}
  button{font:inherit;border:1px solid var(--bd);background:#fff;border-radius:8px;padding:9px 16px;cursor:pointer;font-weight:600;color:#111827}
  button:hover{background:#f9fafb}
  button.primary{background:var(--ac);color:#fff;border-color:var(--ac)}
  button.primary:hover{background:#1d4ed8}
  button.dark{background:var(--ac2);color:#fff;border-color:var(--ac2)}
  button.dark:hover{background:#000}
  button.ghost{color:var(--mut)}
  button:disabled{opacity:.55;cursor:default}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #eef0f3;vertical-align:top}
  th{color:var(--mut);font-weight:700;position:sticky;top:0;background:#fff;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
  tr:hover td{background:#fafbfc}
  .tbl-wrap{max-height:420px;overflow:auto;border:1px solid var(--bd);border-radius:10px}
  .pill{font-size:11px;padding:2px 9px;border-radius:99px;border:1px solid var(--bd);font-weight:600;white-space:nowrap}
  .High{background:#e7f7ec;border-color:#bfe6cb;color:#15803d}
  .Medium{background:#fff6e6;border-color:#f0dfb5;color:#b45309}
  .Low{background:#f3f4f6;color:#6b7280}
  .rm{color:var(--err);cursor:pointer;border:1px solid #f3d3d0;background:#fff;border-radius:6px;font-size:12px;padding:3px 9px;font-weight:600}
  .rm:hover{background:#fdf2f1}
  .muted{color:var(--mut);font-size:13px}
  .stats{display:flex;gap:26px;flex-wrap:wrap;margin-bottom:14px}
  .stat b{font-size:24px;display:block;line-height:1.1}
  .stat span{font-size:12px;color:var(--mut)}
  .links a{color:var(--ac);margin-right:14px;text-decoration:none;font-weight:600}
  .links a:hover{text-decoration:underline}
  #msg{padding:10px 14px;border-radius:8px;margin-bottom:14px;display:none;font-weight:600;font-size:13px}
  #msg.err{background:#fef3f2;color:var(--err);border:1px solid #f3d3d0;display:block}
  #msg.ok{background:#e7f7ec;color:var(--ok);border:1px solid #bfe6cb;display:block}
  .tag{display:inline-block;background:#eef2ff;color:#3730a3;border-radius:6px;padding:1px 8px;font-size:12px;font-weight:600}
  @media(max-width:820px){.grid2{grid-template-columns:1fr}.grid3{grid-template-columns:1fr 1fr}}
</style></head>
<body>
<header><div class="wrap">
  <h1>Outbound List Maker</h1>
  <div class="sub" id="subhead">Loading...</div>
</div></header>
<main>
  <div id="msg"></div>

  <div class="card">
    <h2>Project</h2>
    <div class="row">
      <div style="min-width:300px"><label>Config</label>
        <select id="config" onchange="loadState()"></select></div>
      <div class="muted" id="scopeInfo" style="padding-bottom:8px"></div>
    </div>
  </div>

  <div class="card">
    <h2>Seed the search</h2>
    <p class="hint">Add examples before generating. Seeds can be companies, people, websites, firm types, competitors, or notes. AI uses them as the starting point and finds matching people from there.</p>
    <div class="grid3">
      <div><label>Seed type</label>
        <select id="seedKind">
          <option>Company</option>
          <option>Person</option>
          <option>Website</option>
          <option>Firm type</option>
          <option>Note</option>
        </select></div>
      <div><label>Name</label><input id="seedName" placeholder="Company or person"></div>
      <div><label>Website / source</label><input id="seedWebsite" placeholder="https://..."></div>
    </div>
    <div style="margin-top:12px"><label>Why this is a useful seed</label>
      <textarea id="seedNotes" placeholder="Example: similar buyer, competitor, known good prospect, market segment, person type to copy."></textarea></div>
    <div class="row" style="margin-top:12px">
      <button class="primary" onclick="addSeed()">Add seed</button>
      <button class="ghost" onclick="clearSeeds()">Clear seeds</button>
      <span class="muted">At least one seed is required before AI generation.</span>
    </div>
    <div class="tbl-wrap" style="margin-top:14px;max-height:220px"><table id="seedInputTbl"></table></div>
  </div>

  <div class="card">
    <h2>Generate with AI</h2>
    <p class="hint">Paste the customer/product brief, choose how many people you want, and generate a list of named people with public emails. Needs <span class="tag">ANTHROPIC_API_KEY</span> to be set.</p>
    <div style="margin-bottom:14px"><label>Outbound brief</label>
      <textarea id="brief" style="min-height:130px" placeholder="Paste the product, target customer, geography, relevant buyer workflows, exclusions, and what makes a good prospect."></textarea></div>
    <div class="row" style="margin-bottom:14px">
      <div style="width:170px"><label>Approx. companies</label>
        <input id="companyCount" type="number" min="1"></div>
      <div style="width:190px"><label>People per company</label>
        <select id="contactsPerCompany">
          <option value="3">3 people</option>
          <option value="2">2 people</option>
          <option value="1">1 person</option>
        </select></div>
      <div style="width:200px"><label>When generating</label>
        <select id="aiMode">
          <option value="replace">Replace current list</option>
          <option value="append">Add to current list</option>
        </select></div>
    </div>
    <label>Contact rows by why this person matters</label>
    <p class="hint">Split the list by what separates the person: they can approve buying it, they manage the problem, or they personally do the work your product affects.</p>
    <div class="catgrid" id="catGrid"></div>
    <div style="margin-top:14px"><label>Additional specifications / instructions (optional)</label>
      <textarea id="aiExtra" placeholder="e.g. Only include operators with a public leadership/team page. Avoid investors, agencies, and generic sales contacts."></textarea></div>
    <div class="row" style="margin-top:14px">
      <button class="dark" id="aiBtn" onclick="genAI()">Generate with AI</button>
      <span class="muted">Runs in small batches, so larger lists can take several minutes. Emails must be public; review sources before sending.</span>
    </div>
  </div>

  <div class="card">
    <h2>Add a contact manually</h2>
    <p class="hint">Add one person. Email should be a real public email, not a guessed format.</p>
    <div class="grid3" id="addForm"></div>
    <div class="row" style="margin-top:12px">
      <button class="primary" onclick="addRow()">Add contact</button>
      <button class="ghost" onclick="clearAll()">Clear entire list</button>
    </div>
  </div>

  <div class="card">
    <h2>Current contacts (<span id="count">0</span>)</h2>
    <div class="tbl-wrap"><table id="seedTbl"></table></div>
  </div>

  <div class="card">
    <h2>Build the outbound list</h2>
    <p class="hint">Cleans, removes duplicate contacts, standardizes priorities, and exports. Rows missing a
      source URL or other required fields go to missing_data.csv for review.</p>
    <button class="primary" id="buildBtn" onclick="build()">Build outbound list</button>
    <div id="buildOut" style="margin-top:16px"></div>
  </div>
</main>

<script>
let S = null;
const KEYFIELDS = ["contact_name","contact_email","title","company","firm_type","category","website","linkedin_url","why_relevant","priority","source_url"];
const LABELS = {
  contact_name: "Person name",
  contact_email: "Email",
  title: "Job title",
  company: "Company",
  firm_type: "Firm type",
  category: "Why this person matters",
  website: "Company website",
  linkedin_url: "LinkedIn",
  why_relevant: "Why reach out",
  priority: "Priority",
  source_url: "Source"
};

function msg(text, kind){const m=document.getElementById('msg');m.textContent=text;m.className=kind;
  if(kind==='ok') setTimeout(()=>{m.className='';},3500);}

async function api(path, opts){const r=await fetch(path,opts);const j=await r.json();
  if(!r.ok) throw new Error(j.error||('HTTP '+r.status)); return j;}

async function loadState(){
  const sel=document.getElementById('config');
  const name=sel.value||'';
  S=await api('/api/state'+(name?('?config='+encodeURIComponent(name)):''));
  sel.innerHTML=S.configs.map(c=>`<option ${c===S.config?'selected':''}>${c}</option>`).join('');
  document.getElementById('subhead').textContent=S.project_name+' - '+S.geography;
  const ct=Object.entries(S.category_targets).map(([k,v])=>`${k}: ${v}`).join('  |  ');
  document.getElementById('scopeInfo').innerHTML=`Target people: <b>${S.target_count}</b><br>${ct}`;
  document.getElementById('companyCount').value=S.companies_to_find||12;
  document.getElementById('contactsPerCompany').value=String(S.contacts_per_company||2);
  buildCatGrid(); buildAddForm(); renderSeeds(); renderRows();
}

function buildCatGrid(){
  const g=document.getElementById('catGrid'); g.innerHTML='';
  S.categories.forEach((c,i)=>{
    const v=S.category_targets[c]||0;
    const d=document.createElement('div'); d.className='cat';
    d.innerHTML=`<span>${c}</span><input type="number" min="0" value="${v}" data-cat="${encodeURIComponent(c)}" oninput="sumCats()">`;
    g.appendChild(d);
  });
  sumCats();
}
function catInputs(){return [...document.querySelectorAll('#catGrid input')];}
function sumCats(){
  // Category inputs define total people/contact rows. Approx. companies is separate.
}

function buildAddForm(){
  const f=document.getElementById('addForm'); f.innerHTML='';
  KEYFIELDS.forEach(k=>{
    const wrap=document.createElement('div');
    let field;
    if(k==='category') field=`<select id="f_${k}">`+S.categories.map(c=>`<option>${c}</option>`).join('')+`</select>`;
    else if(k==='firm_type') field=`<select id="f_${k}">`+S.firm_types.map(c=>`<option>${c}</option>`).join('')+`</select>`;
    else if(k==='priority') field=`<select id="f_${k}"><option>High</option><option>Medium</option><option>Low</option></select>`;
    else field=`<input id="f_${k}" placeholder="${LABELS[k]||k.replace(/_/g,' ')}">`;
    wrap.innerHTML=`<label>${LABELS[k]||k.replace(/_/g,' ')}</label>${field}`;
    f.appendChild(wrap);
  });
}

function renderRows(){
  document.getElementById('count').textContent=S.rows.length;
  const cols=["contact_name","contact_email","title","company","firm_type","category","priority","source_url"];
  let h='<tr><th></th>'+cols.map(c=>`<th>${LABELS[c]||c.replace(/_/g,' ')}</th>`).join('')+'<th>why reach out</th></tr>';
  if(!S.rows.length) h+='<tr><td colspan="8" class="muted" style="padding:18px">No contacts yet. Paste a brief and generate, or add one manually.</td></tr>';
  S.rows.forEach((r,i)=>{
    h+=`<tr><td><button class="rm" onclick="del(${i})">Remove</button></td>`+
       cols.map(c=> c==='priority'
          ? `<td><span class="pill ${r[c]||''}">${r[c]||''}</span></td>`
          : (c==='source_url'&&r[c] ? `<td><a href="${esc(r[c])}" target="_blank">link</a></td>`
          : `<td>${esc(r[c]||'')}</td>`)).join('')+
       `<td class="muted">${esc(r.why_relevant||'')}</td></tr>`;
  });
  document.getElementById('seedTbl').innerHTML=h;
}
function esc(s){return (s+'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}

function renderSeeds(){
  const cols=["kind","name","website","notes"];
  let h='<tr><th></th>'+cols.map(c=>`<th>${c}</th>`).join('')+'</tr>';
  if(!S.seed_inputs.length) h+='<tr><td colspan="5" class="muted" style="padding:18px">No seeds yet. Add at least one company, person, website, or note before generating.</td></tr>';
  S.seed_inputs.forEach((r,i)=>{
    h+=`<tr><td><button class="rm" onclick="delSeed(${i})">Remove</button></td>`+
       cols.map(c=> c==='website'&&r[c] ? `<td><a href="${esc(r[c])}" target="_blank">link</a></td>` : `<td>${esc(r[c]||'')}</td>`).join('')+
       '</tr>';
  });
  document.getElementById('seedInputTbl').innerHTML=h;
}

async function addSeed(){
  const seed={
    kind:document.getElementById('seedKind').value,
    name:document.getElementById('seedName').value.trim(),
    website:document.getElementById('seedWebsite').value.trim(),
    notes:document.getElementById('seedNotes').value.trim()
  };
  const j=await api('/api/seed/add',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({seed})});
  S.seed_inputs=j.seed_inputs;
  document.getElementById('seedName').value='';
  document.getElementById('seedWebsite').value='';
  document.getElementById('seedNotes').value='';
  renderSeeds(); msg('Seed added.','ok');
}

async function delSeed(i){
  const j=await api('/api/seed/delete',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({index:i})});
  S.seed_inputs=j.seed_inputs; renderSeeds();
}

async function clearSeeds(){
  if(!confirm('Remove all seeds?'))return;
  const j=await api('/api/seed/clear',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({})});
  S.seed_inputs=j.seed_inputs; renderSeeds(); msg('Seeds cleared.','ok');
}

async function addRow(){
  const row={};
  KEYFIELDS.forEach(k=>row[k]=document.getElementById('f_'+k).value.trim());
  if(!row.contact_name){msg('Person name is required.','err');return;}
  if(!row.contact_email){msg('Email is required for an export-ready person.','err');return;}
  if(!row.company){msg('Company is required.','err');return;}
  const j=await api('/api/add',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:S.config,row})});
  S.rows=j.rows;
  KEYFIELDS.forEach(k=>{const el=document.getElementById('f_'+k);if(el.tagName==='INPUT')el.value='';});
  renderRows(); msg('Added '+row.contact_name,'ok');
}

async function del(i){
  const j=await api('/api/delete',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:S.config,index:i})});
  S.rows=j.rows; renderRows();
}

async function clearAll(){
  if(!confirm('Remove all contacts from the current list?'))return;
  const j=await api('/api/clear',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({config:S.config})});
  S.rows=j.rows; renderRows(); msg('Cleared.','ok');
}

async function genAI(){
  const btn=document.getElementById('aiBtn'); btn.disabled=true; btn.textContent='Generating contacts...';
  if(!S.seed_inputs.length){msg('Add at least one seed before generating.','err');btn.disabled=false;btn.textContent='Generate with AI';return;}
  msg('Generating contacts. This can take a few minutes for larger lists.','ok');
  try{
    const per={}; catInputs().forEach(el=>{const v=parseInt(el.value)||0; if(v>0) per[decodeURIComponent(el.dataset.cat)]=v;});
    const j=await api('/api/generate',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({config:S.config,brief:document.getElementById('brief').value,
        company_count:+document.getElementById('companyCount').value,
        contacts_per_company:+document.getElementById('contactsPerCompany').value,
        per_category:per,extra:document.getElementById('aiExtra').value,
        mode:document.getElementById('aiMode').value})});
    S.rows=j.rows; renderRows(); msg('AI added '+j.generated+' contact rows. Review sources before sending.','ok');
  }catch(e){msg(e.message,'err');}
  btn.disabled=false; btn.textContent='Generate with AI';
}

async function build(){
  const btn=document.getElementById('buildBtn'); btn.disabled=true; btn.textContent='Building...';
  try{
    const j=await api('/api/build',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({config:S.config})});
    let h=`<div class="stats">
      <div class="stat"><b>${j.exported}</b><span>exported</span></div>
      <div class="stat"><b>${j.incomplete}</b><span>incomplete</span></div>
      <div class="stat"><b>${j.duplicates_removed}</b><span>duplicates removed</span></div>
      <div class="stat"><b>${j.exported}/${j.target_count}</b><span>of target</span></div></div>
      <div class="links" style="margin-bottom:12px">Download:
        <a href="/download/outbound_targets.csv">outbound_targets.csv</a>
        <a href="/download/missing_data.csv">missing_data.csv</a>
        <a href="/download/priorities_and_specs.md" target="_blank">priorities_and_specs.md</a>
        <a href="/download/research_log.md" target="_blank">research_log.md</a></div>`;
    const cols=["contact_name","contact_email","title","company","firm_type","category","priority","why_relevant","source_url"];
    h+='<div class="tbl-wrap"><table><tr>'+cols.map(c=>`<th>${LABELS[c]||c.replace(/_/g,' ')}</th>`).join('')+'</tr>';
    j.usable.forEach(r=>{h+='<tr>'+cols.map(c=> c==='priority'
        ? `<td><span class="pill ${r[c]||''}">${r[c]||''}</span></td>`
        : (c==='source_url'? `<td><a href="${esc(r[c])}" target="_blank">link</a></td>`
        : `<td>${esc(r[c]||'')}</td>`)).join('')+'</tr>';});
    h+='</table></div>';
    document.getElementById('buildOut').innerHTML=h;
    msg('Built '+j.exported+' rows.','ok');
  }catch(e){msg(e.message,'err');}
  btn.disabled=false; btn.textContent='Build outbound list';
}

loadState();
</script>
</body></html>"""


def main():
    bt.read_seeds  # ensure module loaded
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Outbound List Maker UI  ->  http://127.0.0.1:{PORT}")
    print(f"AI model: {MODEL}  (set ANTHROPIC_API_KEY to enable AI generation)")
    print("Press Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
