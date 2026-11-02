# Contributing

aicad is AGPL-3.0-or-later. You keep the copyright in what you contribute. Contributions are accepted under the [Contributor License Agreement](CLA.md) and a Developer Certificate of Origin sign-off. The Contributor License Agreement lets the project relicense contributions.

Business documents and personal planning notes do not belong in this repository. That includes notes written by people and notes written by AI agents. Keep them on your own computer under `private/` or `notes/`. Those folders are gitignored. This repository accepts software documentation and the roadmap only. `tools/check_docs.py` enforces that list.

To run the same check on your machine before a commit or a push, once per clone:

```bash
git config core.hooksPath .githooks
```

That setting is optional. `.github/workflows/docs.yml` still runs the check on every push and pull request.

## How to contribute

1. Open an issue or a pull request against `main`.
2. Keep the change focused. Add or update a test when the change has behavior to check.
3. Run `pytest` from a virtualenv with the dev extra installed (`pip install -e ".[dev]"`).
4. Sign off the commit, as below. On your first pull request, sign the Contributor License Agreement, as below.

Discussion and review follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Documentation entries

`docs/DECISIONS.md`, `SECURITY.md`, and the tool table in `README.md` are generated. Add a file in the entry folder. Do not edit those generated documents by hand.

`docs/ROADMAP.md` stays hand-written prose. Edit that file directly.

### Decision file

Name it `docs/decisions/YYYY-MM-DD-short-slug.md`. The slug is a few words from the decision, not a sequence number, so two branches can each add a file when the names differ. The `date` field is that same day. Keep the title and the rationale to one line each: no line break and no HTML comment. Leave `order` unset.

```markdown
---
date: 2026-10-24
---

# The decision in one line

The rationale in one line.
```

### Tool file

Name it `docs/tools/short-slug.md`. Leave `order` unset. The new row is listed after the rows that already have `order`, by file name.

```markdown
# `tool_name`

What the tool does, in one line. No line break and no HTML comment.
```

### Security file

Name it `docs/security/short-slug.md`. Set `order` to a positive integer. The master lists the lowest order first. When two files share an order, the file name breaks the tie, so two branches do not edit one list. A new guarantee uses an order after the last one, or an order between two neighbors. `separator: blank` (the default) leaves a blank line before the next guarantee. Items of one list use `separator: line`. The body is copied into `SECURITY.md`. No HTML comment.

```markdown
---
order: 500
---

The new guarantee, in one paragraph.
```

### A branch that already edited a generated document

Merge `main`. Keep `docs/DECISIONS.md`, `SECURITY.md`, and the generated tool table in `README.md` from `main`. Move a decision into `docs/decisions/`, a tool row into `docs/tools/`, and a security guarantee into `docs/security/`. Leave the generated masters as `main` has them.

`tools/build_docs.py` rebuilds the masters from the entry folders. It does not follow a symlink or a junction, and it does not read or write a file that has another name. `tools/build_docs.py --check` checks the new file. The documentation check fails for a bad entry or a forbidden word. It does not fail because a master document is behind the entry files. It does fail when a pull request edits `docs/DECISIONS.md`, `SECURITY.md`, or the generated tool table. The one exception is the rebuild pull request on `chore/regenerate-master-docs`.

A push to `main` rebuilds the masters and opens or updates one pull request on that branch when they changed. The commit is unsigned and carries a `Signed-off-by` line. The workflow does not merge the pull request. That job runs `python tools/check_docs.py`, `python tools/build_docs.py --check`, and `pytest tests/test_build_docs.py tests/test_docs_allowlist.py` before it opens the pull request, and the pull request links that workflow run. Opening the pull request needs the repository setting **Allow GitHub Actions to create and approve pull requests** (Settings, Actions, General, Workflow permissions). If that setting is off, the job fails and names the setting. A feature pull request adds entry files and does not edit the masters.

## Plugins

A plugin that is loaded into aicad, or that imports aicad, is combined with this program. Distributing that plugin means the AGPL-3.0-or-later obligations apply to it as well. You keep the copyright in the plugin. A plugin you contribute to this repository is accepted under the Contributor License Agreement. The guide is [docs/plugins.md](docs/plugins.md).

## Contributor License Agreement

Your first pull request asks you to accept the [Contributor License Agreement](CLA.md). You keep the copyright in your contribution. The agreement grants a copyright license that lets the project relicense that contribution, and a patent license. You also confirm that the contribution is your original work, or that you have identified the parts that are not, and that you have the right to grant those licenses. A contribution made for an employer or another legal entity needs that entity's permission, or a separate corporate agreement.

Comment on the pull request with this exact text and nothing else:

```text
I have read the CLA Document and I hereby sign the CLA
```

The check stores that signature in this repository on the `cla-signatures` branch, in `signatures/version1/cla.json`. A later pull request from the same account does not ask again. The repository owner and the other accounts named in `.github/workflows/cla.yml` pass without that comment. The workflow does not check out pull request code. Every commit still needs the Developer Certificate of Origin sign-off below.

## Developer Certificate of Origin

Every commit must include a `Signed-off-by` line. That line means you agree to the Developer Certificate of Origin, version 1.1, for that commit.

Git adds the line when you pass `-s`:

```bash
git commit -s
```

The trailer has to match the commit's author name and email:

```text
Signed-off-by: Your Name <you@example.com>
```

Pull requests are checked for this by `.github/workflows/dco.yml`. A commit without the trailer will fail that check. If you forgot it on the latest commit:

```bash
git commit --amend -s
```

The text of the certificate is below. The same text is published at <https://developercertificate.org/>.

## Developer Certificate of Origin, version 1.1

```text
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```
