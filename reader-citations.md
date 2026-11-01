# Pilot citation reader

Status: task-local helper, unreleased. Change log: 2026-10-15 — bounded API
document/chunk read for the already verified P1060 hit. No new search or ingest.

Run in a fresh PowerShell process:

    pwsh -NoProfile -File ./Read-PilotCitation.ps1 -UseProtectedRecovery

The helper pins the installed CLI and existing reader guards, uses the existing
protected reader process-locally, verifies tenant 1 / reader 3 / the existing pilot
KB, then executes only `doc view` and `chunk view` for the known README citation.
No password prompt, profile save, key creation, service change, writer, scheduler,
MCP, model request or search command is provided. Ordinary reader usage timestamps
remain a normal service-side effect. Native failures/timeouts stop without retry.

Output contains API-returned content and chunk identifiers/positions, selected
document metadata and hashes for the explicitly mapped local README files. Local
files never substitute for API content. API positions are not assumed to be
Markdown line numbers. A missing source commit/hash binding leaves freshness
unknown; date comparisons alone do not prove current index contents.

Validation:

    python -X utf8 -B -m unittest discover -s tests -p test_reader_citations.py -v

This is a narrow task helper, not a product release or a claim that every Agent
tool/answer/citation flow has passed. Original project main/index/version and
existing staged work are preserved; task-only Git checkpoints are separate.
