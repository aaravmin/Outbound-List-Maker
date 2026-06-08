# Outbound List Maker

A lightweight local workflow for turning an outbound brief into a sourced,
prioritized contact list.

The app is brief-and-seed driven. It does not preload a customer, target market,
or company list. Add seed companies, people, websites, or notes directly in the
web app, paste the prompt for the market you care about, choose how many people
to generate, then run AI.

## What It Produces

- `data/outbound_targets.csv` — exportable contact rows
- `data/missing_data.csv` — rows missing required fields
- `docs/priorities_and_specs.md` — generated criteria summary
- `docs/research_log.md` — generated run log and review checklist

Each outbound row starts with person name and email, then job title, company,
firm type, website, relevance, priority, and a source URL. Emails should only be
used when there is a public source for the exact email.

## Run The Web App

```bash
export ANTHROPIC_API_KEY='sk-ant-...'
./run.sh
```

Then open `http://127.0.0.1:8000`.

If port 8000 is busy:

```bash
PORT=8500 ./run.sh
```

Generation usually takes 1-4 minutes for 20-40 contact rows because the app makes
a blocking Anthropic API call and asks for sourced contact-level output.

## How To Use It

1. Paste your outbound brief: product, target customer, geography, exclusions,
   and what makes a good prospect.
2. Add seeds directly in the web app. Seeds can be companies, people, websites,
   competitors, known good prospects, or notes about the kind of person you want.
3. Generate around 50 people/contact rows across roughly 20 companies. Use 2 or
   3 contacts per company for normal market research/outbound.
4. Optionally adjust contact rows by why the person matters:
   people who can approve buying, people who manage the problem, and people who
   personally do the work your product affects.
5. Generate with AI. The app uses small batches to avoid partial JSON responses.
6. Review the source URLs and relevance notes.
7. Click **Build outbound list** to clean, dedupe, and export.

## Config

The default config is `config/outbound_list.yaml`. It is generic and can be
edited, but the normal workflow is to paste the live brief in the UI.

The web app stores your generation seeds in `data/seed_inputs.json`. You should
not need to edit a spreadsheet to seed the AI.

The app keeps the current in-progress people list in `data/current_contacts.csv`
and writes final exports to `data/outbound_targets.csv`. You should not need to
edit either by hand during normal use.

The generated contact list uses this header:

```csv
contact_name,contact_email,title,company,firm_type,category,website,geography_relevance,firm_size,linkedin_url,why_relevant,priority,notes,source_url
```

## Command Line

The web app is the easiest workflow. The scripts still work directly:

```bash
python scripts/validate_targets.py
python scripts/build_targets.py
```

Both default to `config/outbound_list.yaml`.

## Review Rules

- Every exported row should be a relevant person, not just a company.
- Every exported row should have a person name and email.
- Prefer 2-3 contacts per company.
- Do not use guessed emails.
- Review `source_url` before sending outreach.
- Move weak or unverifiable rows to `missing_data.csv` or delete them.
