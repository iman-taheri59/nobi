# Versioning and releasing nobitrader

[فارسی](releases.md) · **English**

The current version of this change is **1.0.20**; the previously published tag is
**v1.0.6**, and this change will ship as **v1.0.20**.
`VERSION` is the source of truth for the release number; the tag name is exactly `v`
plus that number.

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
- The product name in the documentation is `nobitrader`. The current spelling in the UI
  stays `nobiTrader`. Nobitex is the exchange's name and does not change.

## Build and verify

From the project root:

```sh
npm ci --no-audit --no-fund
npm run build
python scripts/release_notes.py --tag v1.0.13
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

After review and green tests, commit the related changes and then:

```sh
git tag -a v1.0.13 -m "nobitrader 1.0.13"
```

Only after push is approved:

```sh
git push --atomic origin main v1.0.13
```

If the tag already exists, do not run `git tag` again. Before pushing, check where the
tag points with `git show v1.0.13 --no-patch`. `--atomic` prevents a half-published
branch/tag if one of them is rejected.

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
