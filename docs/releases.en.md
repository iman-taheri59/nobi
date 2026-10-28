# Versioning and releasing nobitrader

[فارسی](releases.md) · **English**

The current version is **2.0.2**; check Releases for actual publication status. Publish it only after CI for that exact commit is green; check Releases for actual publication status.
`VERSION` is the source of truth for the release number; the tag name is exactly `v`
plus that number.

For the previous 2.0.0-to-2.0.1 upgrade path, review the [code-and-state upgrade plan](upgrade-2.0.1.en.md) before producing an installer; changing only the old 2.0.0 installer tag is not sufficient.

## Rules from here on

- Every set of changes — including a documentation-only change — carries a version
  bump and a new section in `CHANGELOG.md` (and `CHANGELOG.en.md`).
- A regular fix: PATCH, e.g. `1.0.9` to `1.0.13`.
- A backwards-compatible feature: MINOR, e.g. `1.0.1` to `1.1.0`.
- A breaking change: MAJOR, e.g. `1.0.1` to `2.0.0`.
- The number must be identical in `VERSION`, `server/main.py`, `package.json`, the two
  package-version fields in `package-lock.json`, the web version chip and the
  standalone output. Dependency and exchange-API versions have nothing to do with the
  product version.
- An annotated tag is created on that same release commit. A published tag is never
  moved, deleted or force-pushed; the next fix gets a new version.
- The product name in the documentation is `nobitrader`; the current spelling in the UI,
  `nobiTrader`, stays. Nobitex is the exchange's name and does not change.

## What is a PATCH and what is a MINOR

SemVer does not treat the third number as a counter — it names the **kind of change**. So the
decision comes from the nature of the change, not from how many releases came before it:

| Change | Number |
|---|---|
| Docs, ADR, CHANGELOG, tests, internal refactor, or fixing wrong behaviour | PATCH — e.g. `1.0.30` → `1.0.31` |
| A new capability that **is still in the shadow**: it adds numbers and evidence, but nothing a user can do changes and `applied` stays `false` | PATCH (explicit shadow-period exception) |
| **Trading behaviour** changes: a gate actually filters, a threshold is applied, a signal or position can differ | MINOR — `1.1.0` → `1.1.0` |
| An incompatible interface or API change | MAJOR — `1.9.10` → `2.0.0` |

The short form: as long as no gate rejects a row, it is the shadow period and a PATCH is enough. The
first gate that really filters is a MINOR — in this project, step S6 of ADR-0023.

## Ceilings

The number has to stay readable, so every field has a ceiling. A ceiling is the **highest value
allowed**, not a count of releases: `1.1.0` is allowed, and the release **after** it is `1.1.0`.

| Field | Ceiling | When it is reached |
|---|---|---|
| PATCH — up to the first crossing of the `1.0.x` line | 30 | the next release is `1.1.0`; PATCH resets to 0 |
| PATCH and MINOR — from `1.1.0` onward | 10 | the next release carries one into the field above and resets the lower one: `1.1.10` → `1.2.0`, `1.10.4` → `2.0.0` |
| MAJOR | 10 | `10.x.y` is allowed; after that `11.0.0` |

- A forced jump from PATCH to MINOR **means the change was not large**; the ceiling simply filled up.
  Say so in that version's `CHANGELOG`: "bumped because of the ceiling, not because behaviour changed".
- The ceilings are enforced by a test (`server/tests/test_release_version.py`), so a number outside the
  rule is rejected in CI.
- If a ceiling ever has to change, the table and that test change together; a ceiling is a contract,
  not a habit.

## Build and verify

From the project root:

```sh
npm ci --no-audit --no-fund
npm run build
python scripts/release_notes.py --tag v2.0.2
python -m pytest -q server/tests
npm run test:display
npm run lint
npm run lint:dashboard
```

The Python test commands need the dependencies in `server/requirements-dev.txt`.
`release_notes.py` checks, without contacting GitHub, that the tag matches the version
and that notes exist for that version. The tag numbers in the examples on this page are
specific to this release; on the next release change them to match `VERSION`.

## Commit, tag and push

These are examples, not permission to execute. After review and tests, obtain
**explicit user approval to push/publish this version**. The normal rule sends
the branch and annotated tag for the same commit together:

```sh
git tag -a v2.0.2 -m "nobitrader 2.0.2"
git push origin main v2.0.2
```

If a tag already exists, never recreate or move it. Inspect it with
`git show v2.0.2 --no-patch`. Tag validation must succeed too; branch CI does not
replace release validation. Publication does not authorize server installation.

The explicitly approved two-stage exception for 2.0.1 (main, green CI, then
annotated tag/Release) is historical and applies only to that release. It does
not authorize 2.0.2 or future publication. The published 2.0.1 tag is unchanged.

## GitHub Release

`.github/workflows/release.yml` runs only when a version tag is pushed:

1. build the standalone file and check that generated outputs are current;
2. check the tag number, the product versions and the changelog notes, and run the
   tests;
3. create the Release titled `nobitrader vX.Y.Z` with the body taken from that
   version's changelog section;
4. attach `nobitrader-mobile.html`; GitHub also exposes the source archive of that tag.

Write permission is granted to the publish job only, using the temporary Actions
`GITHUB_TOKEN`; no PAT is stored in the repository or the workflow. If a repository or
organisation policy restricts creating releases with that token, the Actions run fails
and that policy is what needs looking at. A successful push is not a successful
release; check the result in Actions and on the Releases page. This process does not
deploy to the ArvanCloud server.

Publishing is idempotent: if the release for that tag already exists, the job updates
its notes and re-uploads the mobile file with `gh release upload --clobber` instead of
failing on "already exists". Both paths first verify that the tag points at the commit
the run checked out.

## Legacy technical names

The actual repository URL is still `https://github.com/iman-taheri59/nobi.git`; renaming
it is outside the scope of a documentation fix. Likewise the service name `nobi` and
the real paths `/opt/nobi`, `/etc/nobi` and `/srv/nobi-data` match existing installs and
are deliberately preserved. Replacing those identifiers in the text only would break
the deployment commands.

Folder and domain names in the new Windows install examples are now `nobitrader` and
`nobitrader.test`. An existing install does not need to move its folder or service; run
the commands with your real install path.
