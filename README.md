# Outbound List Maker

A local tool for turning a product/customer brief into a sourced outbound contact
list. Add seed companies, people, websites, or notes directly in the web app;
the AI uses those seeds to shape the generated list. The default output is 50
people, not 50 companies: each row starts with a person name and email, then job
title, company, firm type, source URL, and a short reason that person belongs on
the list.

Run the app:

```bash
cd targetmap-builder
export ANTHROPIC_API_KEY='sk-ant-...'
./run.sh
```

Then open `http://127.0.0.1:8000`.
