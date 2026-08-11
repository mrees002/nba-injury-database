# NBA Injury Dataset: Codex Starter Kit

This folder is designed to be copied into a new Git repository and used as the starting context for Codex.

## Files

- `MASTER_CODEX_PROMPT.md`: overall product and engineering specification
- `CODEX_PROMPTS.md`: staged prompts to run one at a time
- `AGENTS.md`: persistent repository instructions for Codex
- `proposed_schema.sql`: database design reference
- `legacy/process_injuries_pipeline.py`: existing processing logic
- `legacy/WEB_SCRAPER_MIGRATION_GUIDE.md`: prior architecture notes
- `legacy/UPLOADED_FILES_REFERENCE.txt`: inventory of the historical files from the prior workflow

## Recommended workflow

1. Create a new GitHub repository.
2. Copy this starter kit into the repository root.
3. Commit it before asking Codex to make changes.
4. If you still have the historical CSV files described in `legacy/UPLOADED_FILES_REFERENCE.txt`, keep them out of Git at first. Put them in a local `data/import/` directory and add that directory to `.gitignore`.
5. Give Codex Prompt 1 from `CODEX_PROMPTS.md`.
6. Review the diff and test output.
7. Continue through the prompts in order.

Do not begin with the entire product in one task. The staged prompts intentionally force the processing rules to be characterized before the scraper, database, and website are layered on top.

## Historical files still useful

If available, the most useful files to recover from the old project are the raw CSV datasets listed in `legacy/UPLOADED_FILES_REFERENCE.txt`, especially:
- NBA_IL.csv
- NBA_Missed_Games.csv
- later IL incremental CSV files
- later missed-games incremental CSV files

Those files are not included in this starter kit because they were not part of the current uploaded files.

Once recovered, they can serve as:
- historical seed data
- regression/golden-master input
- validation against the database implementation

## Important deployment note

Before running a public automated scraper or redistributing the resulting dataset, review the source site's current Terms of Service and robots.txt and make a deliberate decision about the permitted use. The engineering design should not assume permission.
