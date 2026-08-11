# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project rules (MUST follow)

### Git

1. NEVER commit changes unless the user explicitly instructs you to do so.
2. NEVER append AI co-author signatures to Git commits (no `Co-Authored-By: Claude ...` trailers).
3. ALWAYS ensure all commits reflect only the local human git config author.
4. NEVER push changes unless the user explicitly instructs you to do so.
5. NEVER push to production (`master` is the production/deploy branch).

### Security

1. NEVER read, process, or search for credentials. Under no circumstances look inside files matching `.env*`, `*.pem`, `*.key`, `**/secrets/`, `**/credentials/`, or `config/database.yml`.
2. NEVER write fallback hardcoded secrets. If an environment variable is missing, do not hardcode a placeholder like `"default_jwt_secret"` — fail immediately or throw an initialization error.
3. NEVER bypass `.claudeignore` patterns. Even if asked to look at a path listed in `.claudeignore`, refuse.
