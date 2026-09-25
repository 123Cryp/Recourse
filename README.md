# Recourse

A warranty/service-claim adjudication system built from three GenLayer
Intelligent Contracts that genuinely talk to each other via
`gl.get_contract_at()`, where **consensus strictness is driven by the
counterparty vendor's own claim-outcome history**, not by the claimed
amount and not by the claimant's credibility:

- **VendorLedger** — an append-only per-vendor history of claim outcomes.
  No equivalence principle of its own (pure deterministic arithmetic,
  like a threshold function); exposes `get_vendor_risk(vendor)` (`LOW` /
  `MEDIUM` / `HIGH`) and `record_outcome(vendor, approved)`.
- **ClaimTribunal** — before choosing an equivalence principle, reads the
  vendor's risk from `VendorLedger` with a real `.view()`: `LOW` →
  `strict_eq` on a real web fetch, `MEDIUM` → `prompt_comparative` (two
  independent readings), `HIGH` → `prompt_non_comparative` (a thorough,
  audited single-leader review). After the verdict, writes the outcome
  back to `VendorLedger` with a real `.emit()`, closing the feedback loop.
- **EscalationBoard** — if a claimant disputes a rejection, re-reviews
  under three independent framings via `prompt_comparative`, stricter than
  any of `ClaimTribunal`'s own paths. Writes its (possibly overturned)
  outcome directly to `VendorLedger`, not back to `ClaimTribunal`.

Full architecture details and the justification for each equivalence
principle are in [`DESIGN_DECISIONS.md`](./DESIGN_DECISIONS.md).
Live-verified GenVM findings, including the live end-to-end test proving
two identical claims against differently-rated vendors take two different
EP paths, are recorded in [`LESSONS_LEARNED.md`](./LESSONS_LEARNED.md).

## Deployed addresses (GenLayer Studio)

| Contract | Address |
|---|---|
| VendorLedger | `0xdD40216620a4B3c067860488b33F53e3aa8601B4` |
| ClaimTribunal | `0x92b555764A2b6e356c1446E0239394C78DFEAc3b` |
| EscalationBoard | `0xF08c074adECe794122ce2dE2aBdCd37103263E1f` |

## Access control

Each contract enforces the caller relationships implied by the design:

- `VendorLedger.record_outcome` only accepts calls from the configured
  `ClaimTribunal` or `EscalationBoard` addresses.
- `EscalationBoard.file_escalation` only accepts calls from the configured
  `ClaimTribunal` address.
- `ClaimTribunal.request_escalation` only accepts calls from the claim's
  own recorded claimant, only for a claim whose verdict is `REJECTED`, and
  only once per claim.

All three were exercised live on Studio: an unauthorized direct call to
`VendorLedger.record_outcome` from an unrelated address was rejected with
`UserError("only the configured claim tribunal or escalation board can
record an outcome")`.

## Repository structure

```
contracts/
  vendor_ledger.py
  claim_tribunal.py
  escalation_board.py
tests/
  test_offline.py
DESIGN_DECISIONS.md
LESSONS_LEARNED.md
README.md
```

## Testing

```bash
pip install genlayer-test
pytest tests/ -v
```

The offline suite includes the core property test: two byte-identical
claims against a clean-history vendor and a high-reject-history vendor
must resolve through different equivalence-principle branches.

## Deployment

Use GenLayer Studio (`studio.genlayer.com`). Deployment order matters
because the contracts need each other's addresses, and each one must be
explicitly authorized on the contracts it accepts calls from:

1. `vendor_ledger.py` (no arguments)
2. `claim_tribunal.py` with `vendor_ledger_address`
3. `escalation_board.py` with `vendor_ledger_address`
4. On `ClaimTribunal`, call `set_escalation_board` with the
   `EscalationBoard` address
5. On `EscalationBoard`, call `set_claim_tribunal` with the
   `ClaimTribunal` address
6. On `VendorLedger`, call `set_claim_tribunal` with the `ClaimTribunal`
   address
7. On `VendorLedger`, call `set_escalation_board` with the
   `EscalationBoard` address

Only after all 7 steps are complete will `record_outcome` and
`file_escalation` accept calls (they reject any caller that isn't the
configured contract).

**Important:** keep each `.py` file's header to at most 2 lines of comment
(`# v0.1.0` + `Depends`) — longer comment blocks cause schema-loading to
fail. Details in `LESSONS_LEARNED.md`.
