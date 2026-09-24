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

The only identity under which this repository signs a revocation list:

```
https://github.com/tinylabscom/mvm-images/.github/workflows/revocations.yml@refs/heads/main
```

Issuer: `https://token.actions.githubusercontent.com` (Sigstore keyless OIDC).

The signing job runs in the protected `image-release` environment, from the
`main` branch only, through the manual `Publish revocation list` workflow
(`.github/workflows/revocations.yml`). An on-push or pull-request run cannot
mint this identity: the environment gate requires the same approval that
guards image-set signing.

Consumers must pin this exact identity for the channel — a revocation list
signed by any other workflow, branch, or repository must be refused.

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
3. Run the `Publish revocation list` workflow manually against `main`.

The workflow validates the list, signs it in the protected environment, and
replaces the two channel assets on the `revocations` release.

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
  --certificate-identity "https://github.com/tinylabscom/mvm-images/.github/workflows/revocations.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  revocations.json
```

## Current state

The channel's first publication is an empty list (`revocations: []`),
issued 2026-09-24, valid 45 days. No image set, signer, or pack is currently
revoked.
