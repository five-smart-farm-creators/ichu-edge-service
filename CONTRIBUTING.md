# Contributing

This guide covers how to set up, change, review and release the ICHU Edge Service.

## Prerequisites

- Python 3.13
- Git

## Local setup

```sh
python -m venv .venv
.venv\Scripts\activate           # Windows; on Linux or macOS: source .venv/bin/activate
pip install -r requirements.txt
git config core.hooksPath .githooks
python -m unittest discover -s tests
```

## Project structure and code quality

- One package per bounded context (`iam`, `monitoring`) with `domain`, `application`, `infrastructure`, `interfaces` and, when another context or system is involved, `integration` for anti-corruption layers. `shared` holds technical infrastructure only.
- The domain layer has no Flask, Peewee or HTTP imports; application services never import infrastructure; contexts talk to each other only through an anti-corruption layer.
- Value ranges and invariants live in domain value objects; request models only check shape and types.
- Follow PEP 8 with type hints. Docstrings and comments are in English and short (1–2 lines) and explain why, not what.
- Every behavior change comes with a `unittest` test. Run the suite before opening a pull request.

## Git Flow

Use main for release history and develop for integration. Do not commit directly to either branch.

| Branch | Start from | Pull request target |
| --- | --- | --- |
| feature/<short-description> | develop | develop |
| bugfix/<short-description> | develop | develop |
| fix/<short-description> | develop | develop |
| release/vX.Y.Z | develop | main, then merge back into develop |
| hotfix/vX.Y.Z | main | main, then merge back into develop |

Use lowercase kebab-case branch descriptions. Keep branches focused and delete topic branches after they are merged.

Never open a pull request from `develop` to `main`; releases always go through `release/vX.Y.Z`. Do not use other branch prefixes, such as `docs/`, `refactor/`, `chore/`, or `audit/`.

~~~sh
git switch develop
git pull --ff-only origin develop
git switch -c feature/short-description
~~~

## Commit messages

Write commit messages in English using Conventional Commits:

~~~text
<type>(<scope>): <imperative summary>
~~~

Keep each commit focused and the summary concise, without a trailing period. Common types include feat, fix, docs, style, refactor, perf, test, build, ci, chore, and revert.

~~~text
feat(monitoring): raise geofence alerts from cached rules
fix(iam): reject revoked device credentials
docs: clarify local setup
~~~

Commit messages must also follow these rules:

- Start the summary with a lowercase letter, and keep the header at most 72 characters.
- Use only these scopes: `iam`, `monitoring`, `shared`, `config`.
- Use `feat` only for new functionality, `docs` for documentation and diagram changes, and `chore` for changelog, version, or maintenance changes.
- Do not use the default message of the GitHub web editor, such as `Update file.md`.

Invalid examples:

~~~text
(Docs): Add user personas
Update chapter02.md
feat: add controllers.
faet: add planning service
~~~

## Changelog

Record each logical code, configuration, architecture, or documentation change in CHANGELOG.md under [Unreleased]. Use the applicable Keep a Changelog category and describe the user-visible effect; do not list commit hashes.

## Pull requests

Open feature, bugfix, and fix pull requests against develop. Keep one logical fix per Conventional Commit. Include the purpose, a concise summary, relevant checks, and screenshots for user interface changes. Link related issues when applicable, address review feedback, and do not merge until the required review and checks are complete.

## Commit granularity

A commit is one complete logical change that can be described in a single sentence and leaves the project working.

| Too fine | Balanced | Too coarse |
| --- | --- | --- |
| One commit per folder or file (`feat: add commands models`, `feat: add events models`, ...) | `feat(iot-assets): add device aggregate with commands and events` | `feat: implement iot assets` (a whole bounded context) |
| Several consecutive typo commits | `docs(chapter-ii): add segment 1 interview analysis` | `docs: update report` |

- Code: a feature branch usually has 3 to 8 commits of roughly 3 to 10 files each, split by layer or use case (for example domain model, persistence, REST endpoints, tests).
- Documentation: one commit per complete artifact or section, not per paragraph and not per whole chapter.
- Group typo fixes with the section they belong to; a later round of fixes goes in a single `docs(<scope>): fix typos in <section>` commit.
- Before pushing a branch that only you use, squash local work-in-progress commits into logical commits. Never rewrite commits that are already published.
- The convention is enforced for commits created from 2026-11-07, when it was adopted; older history is kept unchanged.

## Enforcement

- After cloning, run once: `git config core.hooksPath .githooks`. The `commit-msg` hook rejects invalid messages locally.
- The `Git Flow checks` GitHub Actions workflow validates the branch name, the pull request target, and every commit message on pull requests to `main` and `develop`.
- Configure Git with your real name and the email linked to your GitHub account (`git config user.name` and `git config user.email`). `.mailmap` maps previously used alternative identities.
