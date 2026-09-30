# Contributing to Munitas

Thanks for looking at this. It's a young project, so the process below is
deliberately light; expect it to grow as the project does.

## Before you start

- For anything more than a small fix, open an issue first describing what
  you want to change and why. This saves you from building something that
  doesn't fit the direction, and saves a reviewer from untangling a large,
  unexpected diff.
- Read the [walkthroughs](https://albinjacob.github.io/munitas/walkthroughs/feature-walkthrough.html) before
  touching code you haven't worked in before, for what the platform
  actually does before diving into how. Read
  [docs/public/architecture.md](docs/public/architecture.md) for the
  services, trust boundaries, and core guarantees the design rests on.
  Beyond that, every module's own docstring carries the implementation
  detail inline, next to the code it describes, so it can't go stale the
  way a second copy would.
- Read [docs/public/design/governance-model.md](docs/public/design/governance-model.md) before touching
  anything access- or gate-decision-related. The rules there aren't
  incidental; they encode who is legally accountable for what.

## Development setup

See [RUNBOOK.md](RUNBOOK.md) for operational procedures and
`start-dev.ps1` for bringing the stack up locally. The honest current state
of local dev (Windows + WSL2, no cross-platform quickstart yet) is in the
README's [Status](README.md#status) section. If you get a platform-native
setup working on Linux or macOS, that contribution is especially welcome.

## Making a change

1. Fork the repo and branch from `main`.
2. Write the test or verify script first if the change is behavioral:
   this project's [verify/](verify/) suite is the actual source of truth
   for "does this work," not a description in a PR.
3. Run the relevant `verify/*.py` scripts (see
   [run-verification.ps1](run-verification.ps1)) and the web test suite
   (`web/tests/`, via Playwright) before opening a PR.
4. Keep the diff scoped to one concern. A PR that mixes a bug fix with a
   refactor is harder to review and harder to revert if something's wrong.

## Code conventions

- No em-dashes in prose (comments, docstrings, docs, UI copy). Restructure
  the sentence instead.
- UI copy is written for the person doing the job, not for whoever wrote
  the code: no internal jargon, no references to how the system works
  internally.
- A hand-authored diagram under `docs/public/diagrams/` has Mermaid as its
  diffable source, or, where Mermaid's own automatic layout cannot produce
  a clean result, hand-authored draw.io XML with a `.drawio` companion kept
  in sync, per the conventions already in that directory's existing
  diagrams.

## Reporting a bug

Open an issue with: what you did, what you expected, what happened instead,
and the output of the relevant `verify/*.py` script if one covers the area.
A reproduction beats a description every time.

## Security issues

Do not open a public issue for a security vulnerability. See
[SECURITY.md](SECURITY.md) instead.

## Code of Conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md). Participation
means agreeing to it.
