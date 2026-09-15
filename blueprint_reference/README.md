# Integration blueprint reference

This directory contains example code from
[ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint).
It is development reference material, not an installed integration or runtime
dependency. The working ADS integration lives in
[`custom_components/ads_multi`](../custom_components/ads_multi/).
Updating this reference directory does not update the ADS integration.

## Origin and review baseline

- Source repository: https://github.com/ludeeus/integration_blueprint
- Source branch: `main`
- Imported into this project on 2026-05-31 in commit
  `17a8eea9c3379f211fd272d384438b6206976981`.
- The original upstream commit was not recorded and remains unknown.
- GitHub workflows and Dependabot configuration were added separately in
  `57bfaee8664c7b3bba8aaf50dcc7f3eb7b668154`.
- Last upstream review: **2026-09-15**.
- Reviewed upstream commit:
  [`cd399dac2a7e88d899dd62b3f5d8bbf91f1adadd`](https://github.com/ludeeus/integration_blueprint/commit/cd399dac2a7e88d899dd62b3f5d8bbf91f1adadd).

The reviewed commit is a comparison baseline, not the version of the copied
examples or a claim that all upstream changes have been adopted. The initial
import moved existing ADS Python files into `custom_components/ads_multi/`
without changing their contents.

The 2026-09-15 review checked project tooling and example initialization.
Upstream uses split requirements files, enables Renovate by default, and passes
`config_entry=entry` to its example coordinator. These changes have not been
applied here. This project retains its own scripts, Dependabot configuration,
and ADS implementation. Review these existing differences as well as new
upstream changes when planning an update.

## Reviewing and applying upstream changes

1. Check the remotes with `git remote -v`. If `upstream` is absent, add it:

   ```bash
   git remote add upstream https://github.com/ludeeus/integration_blueprint.git
   ```

   Remotes are local Git settings; each fresh clone needs this setup.

2. Fetch upstream and inspect changes since the recorded review:

   ```bash
   git fetch upstream
   git log --oneline cd399dac2a7e88d899dd62b3f5d8bbf91f1adadd..upstream/main
   git diff cd399dac2a7e88d899dd62b3f5d8bbf91f1adadd upstream/main
   ```

   To compare current project tooling with upstream, including older differences:

   ```bash
   git diff HEAD upstream/main -- .github .devcontainer.json .ruff.toml scripts requirements.txt requirements_common.txt requirements_dev.txt requirements_lint.txt
   ```

   Inspect integration examples using `git show`, for example:

   ```bash
   git show upstream/main:custom_components/integration_blueprint/__init__.py
   ```

3. Start with a clean working tree and create an update branch:

   ```bash
   git switch -c maintenance/blueprint-update
   ```

   Apply useful changes selectively. Adapt code patterns to the PLC connection,
   polling, notification, and configuration lifecycle. Do not merge or pull the
   whole upstream branch into this project: the template was imported as files,
   and its example integration is separate from the ADS implementation.

4. If refreshing the copied examples, record their exact source commit here
   separately from the review baseline. Preserve upstream license attribution.

5. For code or tooling changes, run Ruff checks and the repository's Hassfest
   and HACS validation workflows. For integration changes, also verify setup,
   reads/writes, notifications, reload, and unload with multiple PLC entries in
   a development Home Assistant instance.

6. Record the review date, reviewed upstream commit (`git rev-parse upstream/main`),
   changes adopted, and relevant differences left in place. Update the baseline
   hashes in the commands above after completing the review.

There is no automatic template synchronization. Dependabot covers configured
Python and GitHub Actions dependencies, not blueprint source changes; Home
Assistant is explicitly excluded from its Python updates.
