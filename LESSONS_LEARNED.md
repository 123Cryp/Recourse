# Recourse — Lessons Learned (live-verified on GenLayer Studio, Sep 25 2026)

This document records only behaviors that were **live-tested and confirmed
on Studio**, not anything read only in documentation. It carries forward
every constraint already confirmed on AccreditationCheck, Covenant, and
Tribunal (summarized in section 4) plus what is genuinely new to Recourse.

## 1. New finding: branching on a cross-contract `.view()` result works exactly like branching on a constructor value

**Question going in (DESIGN_DECISIONS.md section 6):** Tribunal only ever
used a `.view()` result as prompt *context*; it never used the return value
of a `.view()` call as the condition of an `if`/`elif` that selects which
`eq_principle.*` function to call. Was this safe to rely on without a
prior live check?

**Diagnostic:** two small contracts, `RiskSource` (owner-settable
`get_risk() -> str` returning `"LOW"`/`"MEDIUM"`/`"HIGH"`) and
`RiskBranchCaller` (calls `RiskSource.view().get_risk()` outside any
`nondet` block, then branches on the plain string to call `strict_eq`,
`prompt_comparative`, or `prompt_non_comparative` respectively), deployed
live on Studio:

- `RiskSource`: `0x77760265D3F4DE42F1b10879bC00c68e52406237`
- `RiskBranchCaller`: `0xC4496a3dFb58B95EB36dabc207DA71D62813B971`

**Confirmed live, all three branches:**
| `set_risk` value | EP called | `run_branch_check` result |
|---|---|---|
| `LOW` | `strict_eq` | `SUCCESS`, `"low_path:OK"` |
| `MEDIUM` | `prompt_comparative` | `SUCCESS`, `"medium_path:OK"` |
| `HIGH` | `prompt_non_comparative` | `SUCCESS`, `"high_path:OK"` |

**Conclusion:** `.view()` returns a plain `str`, directly usable in a
comparison — no `unpack_result`-style unwrapping. Deriving a branch
condition from cross-contract data introduces no restriction beyond what
was already known from a transaction-local value (Tribunal's
`claimed_amount` tiering). Safe to rely on in `ClaimTribunal.file_claim`.

## 2. Live end-to-end test: the core Recourse property holds

Full 3-contract deploy + 7-step wiring completed successfully (see
`README.md` for the exact sequence and final addresses). Then, with an
empty ledger:

1. A filler claim against vendor `0x2222...2222` with an evidence URL that
   does not contain the claimed fact → `strict_eq` → `NO_MATCH` →
   `REJECTED` (`claim_id=0`, risk tier at filing time was `LOW`, since the
   vendor had no history yet). This single rejection immediately moved
   `0x2222...2222` to `get_vendor_risk() == "HIGH"` (1 of 1 rejected =
   100% ≥ the 50% high-risk threshold — no need to accumulate several
   claims to cross tiers).
2. Two **byte-identical** claims (same `description`, `claimed_fact`,
   `evidence_url`) filed against `0x2222...2222` (now `HIGH`) and a fresh
   vendor `0x3333...3333` (`LOW`, no history):
   - Against the `HIGH`-risk vendor: `prompt_non_comparative` ran, EP
     output was a full JSON object with a `"reasoning"` field and
     `"final_decision": "REJECTED"`.
   - Against the `LOW`-risk vendor: `strict_eq` ran, EP output was the bare
     string `"NO_MATCH"`, no reasoning field at all.
   The two claims' EP outputs are not just different verdicts but
   different *shapes* — direct on-chain evidence that `VendorLedger`'s
   output actually selected a different code path, not merely different
   prompt content around the same call.
3. `request_escalation` on the rejected `HIGH`-risk claim (`claim_id=1`)
   succeeded (`Return Value: null`, matching the `-> None` signature) and
   fired an async `.emit()` to `EscalationBoard`. The resulting escalation
   record (`get_escalation(0)`) confirmed the rejection on a three-framing
   `prompt_comparative` review: `"overturned": false`,
   `"final_decision": "REJECTED"`.
4. Direct unauthorized call to `VendorLedger.record_outcome` from the
   deployer's own EOA (not through `ClaimTribunal`/`EscalationBoard`) was
   rejected: `Result Code: Rollback`, error message exactly
   `"only the configured claim tribunal or escalation board can record an
   outcome"`. Confirmed to fire regardless of the `approved` argument value
   (the access check runs before any branch on it).

## 3. Confirmed: `.emit()` to `EscalationBoard` completes in a later transaction, as expected

`request_escalation`'s own transaction returned before the escalation
outcome existed; `EscalationBoard.get_escalation(0)` only returned data
after the (separate, async) `file_escalation` call had itself finalized.
Consistent with the async-emit rule already known from Tribunal (section 4)
— no code in Recourse assumes otherwise.

## 4. Prior constraints (from AccreditationCheck/Covenant/Tribunal) reconfirmed, not rediscovered

- Contract header limited to exactly 2 comment lines
  (`# v0.1.0` + `Depends`); no comment block longer than 3-4 consecutive
  lines anywhere else in a file, or schema-loading fails with
  `VM_ERROR: invalid_contract` and empty stdout/stderr.
- Always `raise gl.vm.UserError(...)`, never a bare `UserError`.
- `gl.vm.run_nondet(leader_fn, validator_fn)` positional-only (not used
  directly in Recourse — all three contracts go through the
  `eq_principle.*` wrappers instead, same as Tribunal).
- Constructor address inputs normalized (may arrive as `int`/`str`).
- LLM JSON output stripped of markdown fencing before `json.loads`.
- Raw `int` unsupported for persistent fields — `u256` used throughout.
- `gl.eq_principle.prompt_comparative(fn, principle)` — second argument
  positional, named `principle`, not `task=`.
- `gl.eq_principle.prompt_non_comparative(fn, *, task, criteria)` — these
  two genuinely are keyword-only.
- `gl.get_contract_at(...).view()`/`.emit()` must be called outside any
  `run_nondet`/`eq_principle` block, or `SystemError: 6: forbidden`.
- `.emit()` is asynchronous; no same-transaction read-after-write assumed.
- `gl.message.sender_address` inside a contract reached via `.emit()` is
  the calling contract's address, not the original human sender.
- `gl.message.value` returned with 18 decimals.

## 5. Access control designed in from day one — no steward finding needed this time

Unlike Tribunal (where the first submission was flagged for missing
caller authorization on `record_verdict`/`file_appeal`/`request_appeal`),
Recourse's three equivalent entry points
(`VendorLedger.record_outcome`, `EscalationBoard.file_escalation`,
`ClaimTribunal.request_escalation`) had access control from the first
draft, per `DESIGN_DECISIONS.md` section 5, and the live test in section 2
above confirms the unauthorized-call rejection works exactly as designed.

## 6. Final deployed addresses (Studio, Sep 25 2026)

- `VendorLedger`: `0xdD40216620a4B3c067860488b33F53e3aa8601B4`
- `ClaimTribunal`: `0x92b555764A2b6e356c1446E0239394C78DFEAc3b`
- `EscalationBoard`: `0xF08c074adECe794122ce2dE2aBdCd37103263E1f`

Diagnostic pair (not part of the submitted system, kept for reference):
`RiskSource` `0x77760265D3F4DE42F1b10879bC00c68e52406237`,
`RiskBranchCaller` `0xC4496a3dFb58B95EB36dabc207DA71D62813B971`.
