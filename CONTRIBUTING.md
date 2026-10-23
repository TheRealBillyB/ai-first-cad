# Contributing

aicad is AGPL-3.0-or-later. Improvements stay shared. There is no paid license and no dual license. You keep the copyright in what you contribute. There is no contributor license agreement and no relicensing grant.

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
4. Sign off the commit, as below.

Discussion and review follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Plugins

A plugin that is loaded into aicad, or that imports aicad, is combined with this program. Distributing that plugin means the AGPL-3.0-or-later obligations apply to it as well. You keep the copyright in the plugin. The guide is [docs/plugins.md](docs/plugins.md).

## Developer Certificate of Origin

Every commit must include a `Signed-off-by` line. That line means you agree to the Developer Certificate of Origin, version 1.1, for that commit.

Git adds the line when you pass `-s`:

```bash
git commit -s
```

Pass `-s` only. Do not sign the commit with GPG or SSH.

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
