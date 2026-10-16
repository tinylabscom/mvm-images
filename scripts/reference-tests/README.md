# PS21 one-off Linux reference capture

The frozen v6 runner and three `fixtures/local-producer-*.json` inputs are
independent test data. Inputs are byte-for-byte exports from packet archive
`9d60c70ee3de12fd1d74642497134ef374b20c0bff7b5ce476645ad7fe0678d7`.
Runner SHA-256 is
`49e5ca6a11b624859e51129a27f57f0e247f965776dbd5cab61fdd1c3ddd4a6f`.

The one-off `ps21-checkout-reference-capture.yml` runs the real producer using
the five hash-pinned f5ed151 images source files already in its own checkout.
Curate these additions onto actual images main; the older schema-2 development
checkout cannot run it. Do not restore production source into that older tree.
The hosted Ubuntu 24.04 runner supplies Python 3.12 and venv. Only public pinned
Python dependencies are installed. No mvm checkout, executable, image build,
signing, publication, lock update or merge-queue dependency is involved.

Capture preserves the input files. The runner compares all eight same-subject
manifests and mutation refusals, then emits freshly observed identities. The job
requires three distinct raw-name states and no unsupported-filesystem marker.
The uploaded artifact contains JSON, its SHA-256, input/source hashes, Python
and platform details, pip's dependency download report, installed versions and
the workflow checkout commit. This is test evidence, not an image publication
or boot witness. Review/freeze the artifact before updating consumers; the
baseline Darwin packet intentionally cannot verify supported Linux raw names.

## Coordinator-only execution (not performed during source preparation)

After reviewing only these additions on a fresh branch from current images main:

```sh
git switch -c validation/ps21-checkout-reference-capture
# Apply the narrow reviewed new-file packet, then inspect and commit it.
git push origin HEAD:refs/heads/validation/ps21-checkout-reference-capture
gh run list --repo tinylabscom/mvm-images \
  --branch validation/ps21-checkout-reference-capture \
  --workflow ps21-checkout-reference-capture.yml --limit 5
gh run watch RUN_ID --repo tinylabscom/mvm-images --exit-status
gh run download RUN_ID --repo tinylabscom/mvm-images --dir REVIEW_DIRECTORY
```

Pushing that exact branch boots the new workflow without merging to main.
GitHub normally requires a `workflow_dispatch` workflow to exist on the default
branch before the dispatch API/UI can invoke it; `--ref` alone does not bootstrap
registration. Do not merge just to dispatch. No ordinary PR, main push or
`merge_group` triggers are defined here. Repository policy/Actions availability
and permission to push the one-off branch remain coordinator prerequisites.
Other pre-existing repository workflows may independently react to the push;
review current main's trigger rules before authorization. Do not repeat pushes
unless another capture is intended. Remove the one-off workflow after evidence
collection through the coordinator's normal review process.
