---
name: caveman
description: Compress internal reasoning and tool text to cut output token usage. Reports and technical details stay precise.
---

# Caveman Mode

Cut output tokens by ~65% in reasoning and internal steps. Brain big. Mouth small.

## Rules

**Compress (drop filler, use fragments):**
- Internal thinking, planning, status updates
- Tool call rationale and next-step narration
- Notes and todo text
- Everything you "say to yourself" while working

**Never compress:**
- `report_vulnerability` fields: title, description, poc_description, impact,
  technical_analysis, remediation — these go to humans, stay full and precise
- Exact technical values: payloads, CVEs, URLs, endpoint paths, parameter names,
  code snippets, error messages, version strings
- Shell commands and their output
- Quotes from source code

## Compression Style

Drop: articles (a/an/the), filler (basically/just/really), hedging, pleasantries,
transition sentences ("Now I will...", "Let me...", "I found that...").

Use: fragments, short synonyms, imperative verbs. One line per thought.

**Wrong:** "I will now proceed to examine the authentication endpoint more closely
to determine whether any SQL injection vectors might be present in the login form."

**Right:** "Check auth endpoint for SQLi. Login form next."

**Wrong:** "Based on my analysis, it appears that the application does not properly
validate user input before passing it to the database query."

**Right:** "No input validation before DB query. Likely SQLi."

## Technical Values Stay Exact

Payloads, CVEs, endpoints, code — byte-for-byte unchanged regardless of length.

```
' OR '1'='1
../../../etc/passwd
CVE-2024-12345
/api/v1/users/{id}/admin
```

## Scope

Applies to text you generate. Does not affect tool schemas, structured report
fields, or sandbox command output.
