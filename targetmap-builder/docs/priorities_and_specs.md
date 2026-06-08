# Outbound List — Priorities & Specs

## 1. Objective
Build a sourced, prioritized outbound contact list of 50 contacts in Set in prompt for Your company.

## 2. Company context
- **Company:** Your company
- **Product:** Paste your product, customer, and outbound brief in the web app.
- **Core problem:** The app generates companies and relevant contacts from your brief.
- **Relevant workflows:**
  - buyer research
  - market research
  - outbound prospecting

## 3. Scope
- **Geography:** Set in prompt

## 4. Target categories
- Can approve buying — 15
- Manages the problem — 20
- Does the work — 15

## 5. Required specifications
- Must match the user-provided outbound brief.
- Must include a real company.
- Must include a relevant contact or a specific relevant buyer role.
- Must explain why this contact is relevant.
- Must include a source URL supporting the company, contact, or role relevance.

## 6. Exclusions
- Companies or contacts that do not match the user brief.
- Generic contacts with no clear connection to the buying workflow.
- Guessed email addresses.

## 7. Features captured
- contact_name
- contact_email
- title
- company
- firm_type
- category
- website
- geography_relevance
- firm_size
- linkedin_url
- why_relevant
- priority
- notes
- source_url

## 8. Priority logic
**High**
- Contact directly owns or influences the workflow in the brief.
- Company is a strong fit for the target market.
- Source URL supports the contact or role relevance.

**Medium**
- Contact is plausibly involved but may not own the workflow.
- Company fit is relevant but needs validation.

**Low**
- Contact or company fit is weak, generic, or hard to justify.

## 9. Final output columns
- contact_name
- contact_email
- title
- company
- firm_type
- category
- website
- geography_relevance
- firm_size
- linkedin_url
- why_relevant
- priority
- notes
- source_url
