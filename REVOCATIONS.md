# Revocation channel

Every image-set manifest names this repository's revocation channel:

```
https://github.com/tinylabscom/mvm-images/releases/download/revocations/revocations.json
```

The channel is a pair of GitHub Release assets on the immutable `revocations`
release:

- `revocations.json` — the revocation list itself (JSON, schema below).
- `revocations.json.bundle` — its Sigstore keyless cosign bundle. The list is
  untrusted until the bundle verifies; consumers verify the signature before
  parsing the JSON.

Releases are never deleted or overwritten except that the channel assets are
replaced by the next signed list — the channel is mutable by design, since a
revocation that cannot be updated is no revocation. What is immutable is every
`image-set/v*` release: a revoked set stays published so historical
verification remains possible; it just stops being admitted.

## Signer identity

The only identity pattern under which this repository signs a revocation
list:

```
https://github.com/tinylabscom/mvm-images/.github/workflows/revocations.yml@refs/tags/revocation-list/v<N>
```

Issuer: `https://token.actions.githubusercontent.com` (Sigstore keyless OIDC).

Each publication is signed from a protected `revocation-list/v*` tag, exactly like
an image-set release: the tag must name a commit on `main`, the signing job
runs in the protected `image-release` environment, and the environment's
reviewer must approve the deployment. A dispatch, branch push, or
pull-request run cannot mint this identity — the first publication attempt
from `main` was rejected by the environment's branch policy in under a
second, before any step ran.

Consumers pin the exact tag identity of the list they fetched — a revocation
list signed by any other workflow, tag, or repository must be refused.

## List format (schema version 1)

Exactly the JSON shape `mvm`'s `PackRevocationList` parses
(`deny_unknown_fields`, so unknown keys are refused):

```json
{
  "schema_version": 1,
  "revocations": [
    {
      "key_id": "6996feb9248dd8eee1e335470cbff52a",
      "pack_hash": "optional-sha256-hex-of-one-member-pack",
      "reason": "human-readable, published to consumers verbatim"
    }
  ],
  "issued_at": "2026-09-24T14:35:03Z",
  "not_after": "2026-11-08T14:35:03Z"
}
```

- `key_id` — identifies the signer whose packs are revoked: the first 32 hex
  characters of SHA-256 over the signer's certificate identity string. For a
  keyless image-set release that identity is
  `https://github.com/tinylabscom/mvm-images/.github/workflows/release.yml@refs/tags/image-set/v<VERSION>`;
  its key_id for `image-set/v0.1.0` is `6996feb9248dd8eee1e335470cbff52a`.
  Omitting `pack_hash` revokes everything signed under that `key_id` (the
  whole set, or every set ever signed by that identity); including it revokes
  one member pack.
- `reason` — published verbatim to consumers in the refusal. Write it for the
  operator who sees the failure, not for the responder.
- `issued_at` / `not_after` — RFC 3339 UTC. `not_after` bounds the list's
  validity: consumers refuse a stale list rather than trust it, so a stalled
  pipeline fails closed. The current window is 45 days; renew before expiry.

## Publishing and renewing

The list under `revocations/revocations.json` is the source of truth. To
publish or renew:

1. PR the change to `revocations/revocations.json` (new entries, or a renewed
   `issued_at`/`not_after` pair). The workflow refuses to sign a stale list,
   so renewal cannot be skipped.
2. Merge through the merge queue.
3. Push the next `revocation-list/v<N>` tag naming the merged commit, mirroring
   `just release` for image sets. The tag trigger signs the list keyless in
   the protected environment and replaces the two channel assets on the
   `revocations` release.

The tag binds the signer identity: consumers verify the bundle against the
exact `revocation-list/v<N>` identity, and the environment's deployment reviewer
approves each publication.

## Revoking a set

1. Add an entry naming the set's `key_id` (and `pack_hash` when only one
   member is affected) with a consumer-facing `reason`.
2. Merge and dispatch the workflow as above.
3. Move consumers by updating the image lock in `mvm` through its normal
   review — consumers learn of the revocation at their next verification.
4. Publish a superseding set through the normal release path.

## Verifying the channel by hand

```sh
curl -fLO https://github.com/tinylabscom/mvm-images/releases/download/revocations/revocations.json
curl -fLO https://github.com/tinylabscom/mvm-images/releases/download/revocations/revocations.json.bundle
cosign verify-blob \
  --bundle revocations.json.bundle \
  --certificate-identity "https://github.com/tinylabscom/mvm-images/.github/workflows/revocations.yml@refs/tags/revocation-list/v1" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  revocations.json
```

## Current state

The channel's first publication is an empty list (`revocations: []`),
issued 2026-09-24, valid 45 days, published from tag `revocation-list/v1`. No
image set, signer, or pack is currently revoked.

The first attempt, from a `revocations/v1` tag, signed the list and then
failed to create the `revocations` release: the tag `refs/tags/revocations/v1`
occupied the namespace the release's own `revocations` tag needs. That tag
produced no published asset and was deleted; signing tags use the
`revocation-list/` prefix so the two can never collide again.
