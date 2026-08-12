# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project rules (MUST follow)

### Git

1. NEVER commit changes unless the user explicitly instructs you to do so.
2. NEVER append AI co-author signatures to Git commits (no `Co-Authored-By: Claude ...` trailers).
3. ALWAYS ensure all commits reflect only the local human git config author.
4. NEVER push changes unless the user explicitly instructs you to do so.
5. NEVER push to production (`master` is the production/deploy branch).

### User-completed tasks

1. ALWAYS provide specific, step-by-step instructions for any task only the user can complete — anything requiring an external dashboard, account, or credential the assistant cannot reach (Supabase, Render, DNS, Power BI, cron-job.org). These are tagged **[USER]** in TODO.md.
2. Write the steps as a literal click-path: the exact page or menu to open, the exact button and field labels, the exact values to enter, and how to confirm it worked. Never leave the user to infer a step.
3. When a step needs a value the assistant knows (a URL, a project ref, a CNAME target, a variable name), state that value inline rather than describing it. When a step needs a secret, name the variable and where to get its value — NEVER print the secret itself (see Security rule 1).
4. Say explicitly when a step must be finished before the assistant can continue, and what the assistant needs back from the user (e.g. "paste the Push URL into `POWERBI_PUSH_URL` on Render, then tell me it's set").
5. Do not mark a **[USER]** item complete in TODO.md on the user's behalf. Provide the steps, then wait for the user to confirm they did it.

### Security

1. NEVER read, process, or search for credentials. Under no circumstances look inside files matching `.env*`, `*.pem`, `*.key`, `**/secrets/`, `**/credentials/`, or `config/database.yml`.
2. NEVER write fallback hardcoded secrets. If an environment variable is missing, do not hardcode a placeholder like `"default_jwt_secret"` — fail immediately or throw an initialization error.
3. NEVER bypass `.claudeignore` patterns. Even if asked to look at a path listed in `.claudeignore`, refuse.
