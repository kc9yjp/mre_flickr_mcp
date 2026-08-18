# Releasing

How to cut a release of this project. There's no version bump to make in
code — a release is just a git tag; the workflow does the rest.

## What a release is

Pushing a tag matching `v*.*.*` or `v*.*.*-*` triggers
[`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml),
which builds the multi-stage `Dockerfile` for `linux/amd64` and
`linux/arm64` and pushes it to
[Docker Hub](https://hub.docker.com/repositories/ejwettstein) tagged (via
`docker/metadata-action`'s semver rules) as:

- `{version}` — the full tag, e.g. `v1.0.0-beta.2`
- `{major}.{minor}` — e.g. `1.0` (prerelease tags like `-beta.2` don't get
  a major/minor alias, per semver precedence rules)
- `{major}` — e.g. `1`
- `latest` — always moved to whatever was just pushed, prerelease or not

There is no other release artifact — no GitHub Release, no changelog file,
no package published anywhere else.

## Tag naming

Follow the existing convention (see `git tag -l`): `vMAJOR.MINOR.PATCH`,
optionally with a prerelease suffix — `v1.0.0-alpha.1`, `v1.0.0-beta.1`,
`v1.0.0`. Existing tags run `v0.1.0` → `v1.0.0-alpha.1..8` → `v1.0.0-beta.1`.
`frontend/package.json`'s `"version"` field is **not** kept in sync with
these tags and nothing reads it during release — treat it as vestigial, not
a source of truth.

## Before tagging

Tagging and pushing does **not** wait on CI. `docker-publish.yml` triggers
independently of [`ci.yml`](.github/workflows/ci.yml) — pushing a bad tag
will happily build and publish a broken image to `latest`. So before
tagging:

1. Make sure `main` is at the commit you want to release and CI is green
   there (`pytest`, the Docker build/syntax check in `ci.yml`).
2. If frontend code changed, confirm it builds — see CLAUDE.md's
   [Frontend](CLAUDE.md#frontend-frontend) section
   (`docker build --target frontend`) since `ci.yml` doesn't build it
   separately from the full image.
3. Consider whether [`security.yml`](.github/workflows/security.yml)'s Trivy
   scan and [`secrets.yml`](.github/workflows/secrets.yml)'s secret scan are
   passing on `main` — they don't block tag pushes either, but a container
   with a fresh CRITICAL/HIGH CVE going out as `latest` is worth catching
   first.

## Tagging

```bash
git tag v1.0.0-beta.2
git push origin v1.0.0-beta.2
```

Then watch the **Publish to Docker Hub** workflow run in the Actions tab.
Once it finishes, `docker pull ejwettstein/flickr-mcp:latest` (or the
specific version tag) picks up the new image.

To remove a bad tag (before or after it triggered a publish — re-pushing
the corrected tag re-triggers the workflow and moves `latest` again):

```bash
git tag -d v1.0.0-beta.2
git push origin :refs/tags/v1.0.0-beta.2
```

## Related docs

- [readme.md](readme.md) / [DOCKER_HUB_README.md](DOCKER_HUB_README.md) — what the published image is and how users run it
- [ARCHITECTURE.md](ARCHITECTURE.md#deployment) — how the Dockerfile itself is structured
