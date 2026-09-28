# Recourse — Lessons Learned (live-verified on GenLayer Studio, Sep 25 2026)

This document records only behaviors that were **live-tested and confirmed
on Studio**, not anything read only in documentation. It carries forward
every constraint already confirmed on AccreditationCheck, Covenant, and
Tribunal (summarized in section 4) plus what is genuinely new to Recourse.

## 1. New finding: branching on a cross-contract `.view()` result works exactly like branching on a constructor value

**Question going in (from the original design document):** Tribunal only ever
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

## 2. Live end-to-end test on the first deployment (superseded)

This section records the first deployment (addresses in section 8),
before the evidence-grounding redesign of section 7. Full 3-contract
deploy + 7-step wiring completed successfully. Then, with an empty
ledger:

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

## 6. `genlayer-test` 0.29.2 Direct Mode: abandoned in favor of a custom offline stub

Initial offline testing used `genlayer-test`'s Direct Mode (`pytest` +
`direct_deploy`/`direct_vm` fixtures). Several real, environment-level
issues were hit in sequence, none caused by Recourse's contract code:

- `genlayer_py` requires `collections.abc.Buffer`, Python 3.12+ only --
  CI must not use 3.11.
- The GenVM runtime artifact `genlayer-test` 0.29.2 tries to download
  (`genvm-universal.tar.xz` from release `v0.3.0-rc7`) doesn't exist
  under that name in the actual GitHub release (which ships
  `genvm-runners-all.tar.xz` instead) -- a 404 unless pre-cached
  manually under the expected filename.
- A module-level singleton (`genlayer.gl.genvm_contracts.__known_contract__`)
  meant to catch two `gl.Contract` subclasses in one file also fires
  across separate `direct_deploy(...)` calls for *different* contract
  files in one test -- blocking any multi-contract test -- worked
  around by resetting it before each deploy in a custom `conftest.py`.
- After that fix, a deeper, unresolved bug remained: a freshly deployed
  contract's own `set_*`-once method (e.g. `EscalationBoard.set_claim_tribunal`)
  reported "already set" on its very first call, within the *first*
  test in the file to ever wire up the three contracts together -- ruling
  out cross-test state leakage. No further diagnosis was possible
  without the SDK's own source (not accessible in this environment), and
  `genlayer-test` has open GitHub issues describing other regressions
  around this same version boundary.

Given the exact same access-control logic (§2 above) was already
independently live-verified correct on real GenVM via Studio, further
chasing this Direct Mode bug wasn't a good use of time. The offline
suite was rewritten against a small, self-written `genlayer` SDK stub
(`tests/genlayer_stub/`, following the pattern already used successfully
on several earlier projects, e.g. TrueStake) instead, extended to
support `gl.get_contract_at(...).view()/.emit()` via a simple address
registry and a call stack that tracks which contract is "currently
executing" so `.emit()` can correctly report the calling contract's own
address as `gl.message.sender_address` inside the callee -- reproducing
the one real cross-contract behavior (§5 above / rule confirmed on
Tribunal) that actually matters for these tests, without depending on
`genlayer-test`'s Direct Mode internals at all. Writing this stub itself
surfaced one bug worth noting for future stubs: the `.emit()` sender
override must assign to the `gl.message` *instance* attribute, not the
`_Message` *class* attribute -- once `set_caller()` has been called once,
it shadows the class attribute with an instance attribute, so writing to
the class afterwards silently has no effect on what code actually reads.
All 19 tests pass with this stub, no install step required.

## 7. Steward rejection, and the three rounds of redesign that followed

The first submission was rejected with this feedback:

> "The claim verdicts are not grounded in authoritative claim or policy
> evidence, and the high-risk validation can accept materially conflicting
> decisions when each has internally coherent reasoning. A future
> submission should acquire and normalize the evidence needed for every
> adjudication path and require validators to independently verify the
> same consequential verdict against that evidence."

It was right. Only the LOW tier ever fetched `evidence_url`; MEDIUM and
HIGH judged the claimant's own words. The fix took three rounds, each
driven by something only a live test or an outside review exposed:

**Round 1 (second deployment, section 8).** Every tier fetched the page
and required a quote. Live testing then showed I had misused
`prompt_non_comparative`: per the SDK, its function only supplies the
*input* and the SDK's own model performs the task, so a guard inside the
function was bypassed, the output arrived in a markdown fence that a
plain `json.loads` silently turned into `REJECTED`, and a supported claim
was rejected after two leader rotations. Fixed on a probe first
(rotations 2 → 0). Lesson: the first grounding test (persuasive but
unsupported claim → `REJECTED`) passed while HIGH rejected everything;
only a test in the other direction exposes that. Test both directions.

**Round 2.** Re-reading the feedback against the code found that
evidence was still fetched separately inside each verdict call, HIGH
still relied on validators judging a leader's output rather than
re-deriving it, and sources were not restricted at all. Evidence
acquisition became its own `strict_eq` stage; all tiers moved to
independent comparative readings; LOW gained a model reading because
substring matching approved a claim the page contradicted ("Avoid use in
operations" matched "use in operations"); claimant text could forge an
`<evidence>` block in the prompt; escalation double-counted every
appealed claim in the ledger (visible in section 8's numbers, which I had
wrongly reported as intended).

**Round 3 (after an external review, section 9).** Verdict comparison
moved from a model-judged principle to a custom validator with exact
equality; sources moved from domains to owner-approved documents per
vendor; unreadable model output stopped counting against vendors; ledger
corrections were tied to claim ids.

## 8. Second deployment (superseded): live results and addresses

Deployed after the section 7 redesign (Studio, Sep 28 2026), wired with
the same 7-step sequence; the decoded wiring arguments were checked
against the intended addresses.

- `VendorLedger`: `0xD737A1e6cFf53432435B2A1651b9E0c01A30aA89`
- `ClaimTribunal`: `0x53Fd5b2FbA01A012E370027A5FD366a266ca2562`
- `EscalationBoard`: `0x6B8137514D447D14dD796476eACf8f0FF3D06e10`

**Verified live on this deployment** (`Rotation Count: 0` unless
noted):
- LOW: an unsupported claim -> `REJECTED` (`strict_eq`, `NO_MATCH`); the
  single rejection moved the vendor to `HIGH`.
- HIGH, both directions: a claim the page supports -> `APPROVED` with a
  verbatim quote; a persuasive claim the page does not support ->
  `REJECTED` with an empty quote. Stored `risk_tier`/`verdict` matched
  the equivalence-principle output.
- MEDIUM, both directions: a vendor at 2 approved / 1 rejected (33%)
  reached `MEDIUM`; a supported claim -> `APPROVED` (rotation 0), and an
  unsupported claim -> `REJECTED`, both with the MEDIUM-specific
  `literal_reading`/`reasonable_reading` fields in the output. The
  rejection needed `Rotation Count: 2` (see the stability note below).
- Escalation: the rejected claim's escalation was upheld by all three
  framings with a real quote (`overturned: false`), and the vendor's
  ledger updated to 1 approved / 3 rejected, i.e. the outcome was written
  to `VendorLedger`, not `ClaimTribunal`.
- Access control, negative calls from an unrelated wallet, both rolled
  back with the expected messages: `VendorLedger.record_outcome` (with
  `approved = true`) and `EscalationBoard.file_escalation`.
- Fetch failure fails closed: a claim with `evidence_url =
  https://example.invalid/x` made the runtime raise `NondetException`
  (`TLD_FORBIDDEN`), the whole `file_claim` transaction errored
  (`Contract Error`, `exit_code 1`), no claim was recorded
  (`get_claim_count` stayed at 11), and the vendor's ledger entry stayed
  at 0 approved / 0 rejected. An unreachable page therefore cannot count
  against a vendor.
- The transaction list for `ClaimTribunal` shows one `record_outcome` per
  filed claim, all finalized.

**Not tested live on this deployment:** the claimant-only restriction on
`request_escalation` (needs a second wallet; covered offline), an
escalation that overturns a rejection,
fetch failures other than a forbidden top-level domain (e.g. an HTTP
error on a valid domain), repeat-run consistency of
the same claim, and a supported LOW-tier approval as a standalone test (it occurred while building the MEDIUM vendor's history,
consistent with the ledger's counts, but was not inspected on its own).

**Stability note:** one MEDIUM rejection needed two leader rotations
before validators agreed (the other MEDIUM, LOW, HIGH and escalation
transactions finished with none). The decision was correct and the cause
was not investigated, so treat comparative-path latency as variable. The
`quoted_evidence` on that rejected claim was a real sentence from the page
but unrelated to the claimed fact; for a rejection this is harmless, and
it is an instance of the known limit that code checks a quote's presence,
not its relevance.

**A note on test inputs:** one claim was filed with the table's expected
result pasted into the `claimed_fact` field
(`... documentation examples APPROVED (LOW)`). The LOW tier correctly
rejected it, since the extra text is not on the page. The Studio form
also splits arguments on commas, which truncated one description.

**First deployment (superseded, section 2):**
`VendorLedger` `0xdD40216620a4B3c067860488b33F53e3aa8601B4`,
`ClaimTribunal` `0x92b555764A2b6e356c1446E0239394C78DFEAc3b`,
`EscalationBoard` `0xF08c074adECe794122ce2dE2aBdCd37103263E1f`.

Diagnostic contracts (not part of the submitted system): `RiskSource`
`0x77760265D3F4DE42F1b10879bC00c68e52406237`, `RiskBranchCaller`
`0xC4496a3dFb58B95EB36dabc207DA71D62813B971`, and the corrected
`EvidenceProbe` `0x6dfCD396A76C8f6b33454004461136026d9cBe3a`.

## 9. External review (ChatGPT), and what was done with it

An independent review of the round-2 contracts listed 22 findings. Each
code-level claim was reproduced against the contracts before changing
anything; five were confirmed as real bugs (unparseable model output
counted as a rejection against the vendor; any page, subdomain, port or
plain-http URL on an approved domain was accepted; an early overturn could
be absorbed by an unrelated later rejection) and fixed with tests that fail
on the old code. Its main design point, that verdict agreement was judged
by a model rather than compared exactly, matched the official docs'
default recommendation and was adopted. A second review of the new code
agreed the validator follows the documented pattern and asked for a live
test proving a validator's "no" really blocks a result; that is row 4 of
section 10. Findings that are design limits rather than bugs are listed in
`DESIGN_DECISIONS.md` section 7. The reviewer also correctly narrowed my
own claim: fuzzing proves structural properties (a quote is present,
readings agree), not that a quote supports a claim.

## 10. Third deployment: probe and live results

**Probe first** (`diagnostics/evidence_probe.py`, built from the
contract's own helper code), `0x0Dd781FC5c913C0DD236De745320b008dd55B56c`:

| Test | Result |
|---|---|
| HIGH, supported claim | `APPROVED`, rotation 0 |
| LOW, "use in operations" (page says "Avoid use in operations") | `REJECTED`, rotation 0 |
| MEDIUM, supported claim | `APPROVED`, rotation 0 |
| validator that always votes no | `UNDETERMINED` after 3 rotations |
| `get_last` afterwards | still the MEDIUM result; the vetoed write never happened |

An earlier probe (`0xAE0A2D5b5e55a2B0828C940696A3f9F20bA46272`) confirmed
`strict_eq` agreement on normalized page text for example.com (127
characters) and a larger iana.org page (1243 characters), rotation 0.

**Deployment** (Sep 28 2026), wired in 8 steps; the decoded address
argument of every wiring step was checked against the intended contract,
and steps 4 and 5 ran the new same-ledger check live.

- `VendorLedger`: `0x3133582507b2aD607E84e2E572707498B975A98a`
- `ClaimTribunal`: `0x44edF61A3229d03D826344aa659F5a2b080cf3a7`
- `EscalationBoard`: `0x651F19cE58B0610004Ae31149d4c6c80e59BEb3B`

Vendors: A `0xdddd…dddd`, B `0xeeee…eeee`, both with source
`https://example.com/`.

| Test | Result |
|---|---|
| `check_evidence_url` A, before approval | `UNTRUSTED` |
| after approval: `https://example.com` / `http://…` / `https://www.…` | `TRUSTED` / `UNTRUSTED` / `UNTRUSTED` |
| claim 0, A, supported (LOW) | `APPROVED`, rotation 0; ledger A 1/0 |
| claim 1, B, unsupported (LOW) | `REJECTED`, rotation 0; ledger B 0/1, `HIGH` |
| claim 2, B, supported (HIGH) | `APPROVED`, `risk_tier: HIGH`, evidence and decisive quote stored; ledger message carried `claim_id = 2` |
| appeal of claim 1 | upheld, `overturned: false`; ledger B stays 1/1; claim 1 status `R` |
| wallet → `record_outcome` | rolled back, "only the configured claim tribunal can record an outcome" |
| wallet → `record_overturn` on claim 1 | rolled back, "only the configured escalation board can record an overturn" |
| wallet → `file_escalation` | rolled back, "only the configured claim tribunal can file an escalation" |

Not tested live on this deployment: an appeal that overturns (covered
offline, including out-of-order delivery), the MEDIUM tier on the full
system (the same code path passed on the probe), unreadable model output
(cannot be forced live; covered offline), and the claimant-only rule for
escalation (needs a second wallet; covered offline).

