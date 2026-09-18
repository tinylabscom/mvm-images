# Security policy

## Reporting

Report suspected compromise of a published image set, a signing identity, or
this repository's workflows through GitHub private vulnerability reporting on
this repository. Do not open a public issue for an unpublished vulnerability.

For anything in the mvm host or guest runtime rather than the images, report it
against [tinylabscom/mvm](https://github.com/tinylabscom/mvm) instead.

## What is in scope

- Bytes published by this repository's releases.
- The manifest, its signature, and the checksum documents that cover an image set.
- The workflows that build, sign, and publish them, and their permissions.
- The revocation channel.

## What we guarantee about published artifacts

- Every published set is signed by a workflow in this repository, running from a
  protected tag, in a protected environment. The certificate identity is bound
  to that workflow and tag, and consumers check it.
- Releases are immutable. Nothing is deleted or overwritten. A bad set is
  superseded by a new one plus revocation metadata.
- No secret or customer data is ever published. Workflows hold only the
  short-lived OIDC token keyless signing needs.

## Handling a compromised set

1. Publish revocation metadata naming the affected set.
2. Publish a superseding set built from reviewed inputs.
3. Move consumers by updating the image lock in `mvm`, through review.

A revoked set stays published so historical verification remains possible; it
just stops being admitted.
