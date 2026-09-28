# Recourse

Three GenLayer Intelligent Contracts that adjudicate warranty/service
claims. **How much independent agreement a claim needs is set by the
vendor's claim history**: one reading for a clean history, two for a mixed
one, three for a poor one. Every verdict is decided from a page the
contract fetches itself, from a source the owner approved for that
vendor, and each validator re-derives the verdict independently and must
reach exactly the same decision.

- **VendorLedger**: per-vendor claim history and per-claim status; each
  claim counts exactly once, including after an appeal.
- **ClaimTribunal**: checks input and evidence source, acquires the
  evidence with `strict_eq`, decides with a custom validator
  (`run_nondet_unsafe`), stores the evidence and the decisive quote, and
  reports the outcome to VendorLedger.
- **EscalationBoard**: re-reviews a rejected claim under three framings on
  the stored evidence, and reports only an overturn to VendorLedger.

Design and its limits: [`DESIGN_DECISIONS.md`](./DESIGN_DECISIONS.md).
History, steward feedback and every live test:
[`LESSONS_LEARNED.md`](./LESSONS_LEARNED.md).

## Deployed addresses (GenLayer Studio, third deployment)

| Contract | Address |
|---|---|
| VendorLedger | `0x3133582507b2aD607E84e2E572707498B975A98a` |
| ClaimTribunal | `0x44edF61A3229d03D826344aa659F5a2b080cf3a7` |
| EscalationBoard | `0x651F19cE58B0610004Ae31149d4c6c80e59BEb3B` |

Earlier deployments are superseded; see `LESSONS_LEARNED.md`.

## Verified live on this deployment

- Evidence sources: an unapproved vendor was refused; after approval
  `https://example.com` was accepted while `http://example.com` and
  `https://www.example.com` were refused.
- Supported claim, LOW tier → `APPROVED`; unsupported claim → `REJECTED`,
  which moved that vendor to HIGH; supported claim against the HIGH vendor
  → `APPROVED` with `risk_tier: HIGH`, the full evidence text and the
  decisive quote stored on the claim. All without leader rotation.
- Outcomes reached VendorLedger with their claim id.
- An appeal of the rejected claim was upheld and the vendor's counts did
  not change (the previous deployment double-counted here).
- Direct calls from a wallet to `record_outcome`, `record_overturn` and
  `file_escalation` were all rolled back with the expected messages.
- On a separate probe contract: a validator that always votes no ended
  the transaction `UNDETERMINED` after three rotations, and state was not
  written.

## Testing

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
python3 tests/fuzz_invariants.py
```

No install step: the suite runs against a small offline `genlayer` stub
in `tests/genlayer_stub/` whose `run_nondet_unsafe` runs the contract's
real validator against the leader's result. 41 tests; every protection
was also checked by removing it and confirming a test fails (40
mutations). `fuzz_invariants.py` checks structural invariants of the URL
parser, the verdict interpreter and the ledger on random inputs. What
offline tests cannot show (multiple validators, rotation, whether a quote
truly supports a claim) is covered by the live tests above or listed as a
limitation.

## Deployment

1. `vendor_ledger.py` (no arguments)
2. `claim_tribunal.py` with `vendor_ledger_address`
3. `escalation_board.py` with `vendor_ledger_address`
4. `ClaimTribunal.set_escalation_board(escalation_board_address)`
5. `EscalationBoard.set_claim_tribunal(claim_tribunal_address)`
6. `VendorLedger.set_claim_tribunal(claim_tribunal_address)`
7. `VendorLedger.set_escalation_board(escalation_board_address)`
8. `ClaimTribunal.add_vendor_source(vendor, "https://.../")` per vendor

Steps 4 and 5 refuse to connect contracts that use different ledgers.
Keep each contract's header to the two comment lines it has.

## Repository structure

```
contracts/     vendor_ledger.py, claim_tribunal.py, escalation_board.py
tests/         test_offline.py, fuzz_invariants.py, _bootstrap.py, genlayer_stub/
diagnostics/   evidence_probe.py (the live probe used before deployment)
DESIGN_DECISIONS.md
LESSONS_LEARNED.md
```
