# Outbound List Maker

A local tool for turning a product/customer brief into a sourced outbound contact
list. Add seed companies, people, websites, or notes directly in the web app;
the AI uses those seeds to shape the generated list. The default output is 50
people, not 50 companies: each row starts with a person name and email, then job
title, company, firm type, source URL, and a short reason that person belongs on
the list.

I hated finding relevant contacts to reach out to everytime I wanted to conduct market research for a product of mine. Outbound List Maker is a tool I use to automate that proces.

Made for generating outbound list for InspectMind AI (YC W24)

Run the app:

```bash
cd targetmap-builder
export ANTHROPIC_API_KEY='sk-ant-...'
./run.sh
```

Then open `http://127.0.0.1:8000`.
