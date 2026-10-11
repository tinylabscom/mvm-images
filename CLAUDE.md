# mvm-images guidance

Read and follow `AGENTS.md` for ownership, contracts, networking, release
prerequisites and validation boundaries.

Only the Linux layer belongs here, from local Nix definitions. No mvm source
input, binary, authored `/init` or runtime role. Complete signed runtime
ships with mvmctl. Default/rootless profiles remain generic, separate and
NIC-less with authenticated FlowMux over vsock.

New manifests use schema 3/base contract 1; historical signed schema 2 sets
stay untouched. Base marker bytes are `1\n`; identities are 0/901/987/988/989,
plus builder UID 902 and ABI 2. Keep 17 producer Linux members including dev
variants; 13 is only the consumer minimum. Publication/provenance/reproduction
use the member table.

Migration is staged, not released. No compatible CLI v0.23.1 is published
yet. Consumer lands before CLI tag; CLI and signed full runtime publish and
installed boots are witnessed before Linux-only publication. Only afterward
may mvm's images.lock advance. Never invent release evidence or bypass gates.

Preserve signing, revocation and reproduction. Changed instruction files
require real authorized re-signing of detached Sigstore bundles; never forge
them or claim stale bundles authenticate changed text.
