# Recourse — Design Decisions

This document describes the design that is deployed now (third deployment,
Sep 28 2026). How the design got here, including a steward rejection and
two superseded deployments, is recorded in `LESSONS_LEARNED.md`.

## 1. The mechanism

Recourse adjudicates warranty/service claims. **How much independent
agreement a claim needs depends on the vendor's claim history**, not on
the claimed amount and not on the claimant:

| Vendor history (share of past claims rejected) | Tier | Independent readings that must all approve |
|---|---|---|
| none, or under 20% | LOW | 1 (strict literal) |
| 20% to under 50% | MEDIUM | 2 (+ reasonable person) |
| 50% or more | HIGH | 3 (+ skeptical) |

Two byte-identical claims against vendors with different histories take
different paths, observable in the stored `risk_tier` and in the prompt
each tier sends. The outcome of every claim is written back to the
vendor's history, so it affects the next claim against that vendor.

What the tier means, stated precisely: the history counts how often past
claims **against this vendor** were not supported by evidence. A high
rate means stronger agreement is required before approving the next
claim. It is not a finding that the vendor misbehaved. Consequences of
this choice are listed in section 7.

## 2. How a claim is decided

Every claim goes through the same four stages. Only the number of
readings changes by tier.

1. **Input checks (plain Python, before anything is recorded).** The
   claimed fact must be 15 to 300 characters, the description at most
   1000, and the evidence URL must be an approved source for that vendor
   (section 3). Any failure raises `UserError`; nothing is stored.
2. **Evidence acquisition (`strict_eq`).** Leader and validators each fetch
   the page and normalize it the same way (collapse whitespace, replace
   `<`/`>`, cap at 6000 characters). They must produce byte-identical
   text, which becomes the single evidence text for the rest of the claim.
3. **Verdict (`gl.vm.run_nondet_unsafe` with a custom validator).** The
   leader asks the model for the tier's readings, a verbatim quote and a
   final decision, then plain Python interprets the reply:
   - unparseable reply or unknown decision → `INVALID`;
   - `APPROVED` is kept only if every required reading says `APPROVED`
     and the quote (normalized, at least 15 characters) is found in the
     evidence text; otherwise it becomes `REJECTED`.

   Each validator checks the leader's result itself (a valid decision, and
   for an approval a quote that really is in the evidence), then **runs the
   whole reading again independently** and votes yes only if its own
   `final_decision` is exactly equal to the leader's. There is no
   model-based comparison of verdicts.
4. **Recording.** `APPROVED`/`REJECTED` is stored on the claim together
   with the full evidence text and the decisive quote, and sent to
   `VendorLedger` with the claim id. `INVALID` raises `UserError`: no claim
   is stored and the vendor's history is not touched, so the claimant can
   simply file again.

Why this answers the steward's feedback (quoted in `LESSONS_LEARNED.md`
section 7):

- *"acquire and normalize the evidence needed for every adjudication
  path"*: stage 2 runs for every tier and for no other purpose, and the
  exact text is stored on the claim.
- *"require validators to independently verify the same consequential
  verdict against that evidence"*: stage 3 re-derives the verdict on every
  validator from the same agreed text and requires exact equality.
- *"can accept materially conflicting decisions when each has internally
  coherent reasoning"*: an approval that a required reading dissents from
  is overridden in code, and a leader/validator disagreement is a failed
  consensus round, never an accepted result.

## 3. Approved evidence sources

The contract owner approves evidence **documents**, per vendor, with
`add_vendor_source(vendor, source_url)` and can revoke them with
`remove_vendor_source`. A claim's `evidence_url` is accepted only if:

- it starts with `https://`, is at most 500 characters, and contains no
  query, fragment, backslash or whitespace;
- its host has no port, user info, brackets or `%`, and matches the
  approved host **exactly** (subdomains are not covered);
- its path equals an approved source path, or lies under an approved path
  that ends in `/` (so `https://vendor.com/warranty/` covers
  `/warranty/terms` but not `/warrantyX` or `/blog`);
- the path has no `.`/`..` segments and no encoded `.`, `/` or `\`;
- the source was approved **for this vendor**.

`check_evidence_url(vendor, url)` is a free view that answers the same
question.

## 4. The three contracts

**VendorLedger.** Per-vendor approved/rejected counts and a per-claim
status (`A`, `R`, `O` overturned, `P` overturn waiting for its claim).
`record_outcome(vendor, approved, claim_id)` is accepted only from the
configured ClaimTribunal and only once per claim.
`record_overturn(vendor, claim_id)` is accepted only from the configured
EscalationBoard and only for a claim that is rejected (it moves that one
count from rejected to approved) or not yet recorded (it waits, and is
applied when that claim's outcome arrives). So each claim counts exactly
once, whatever order the asynchronous messages arrive in. No equivalence
principle: this is deterministic bookkeeping.

**ClaimTribunal.** Section 2, plus `request_escalation(claim_id)`: only the
claim's own claimant, only for a `REJECTED` claim, only once.

**EscalationBoard.** Re-reviews a rejected claim under three framings
(literal, intent, adversarial) with the same verdict mechanism as section
2 stage 3, on the **evidence stored in the claim record** (it never
refetches, so the appeal judges exactly what the first verdict judged).
Upheld: nothing changes in the ledger. Overturned: `record_overturn` for
that claim. Unreadable model output is recorded as `INCONCLUSIVE` and
leaves the rejection standing, instead of failing and leaving the claim
marked as escalated with no appeal record.

## 5. Wiring and access control

Designed in from the start (the lesson of Tribunal), and tightened after
review:

| Write method | Who may call it |
|---|---|
| `VendorLedger.record_outcome` | configured ClaimTribunal |
| `VendorLedger.record_overturn` | configured EscalationBoard |
| `EscalationBoard.file_escalation` | configured ClaimTribunal |
| `ClaimTribunal.request_escalation` | the claim's claimant, once, rejected claims only |
| `add_vendor_source` / `remove_vendor_source` | owner |
| all `set_*` wiring methods | owner, once |

`ClaimTribunal.set_escalation_board` and `EscalationBoard.set_claim_tribunal`
call `get_vendor_ledger()` on the other contract and refuse unless both
use the same ledger, so a wallet address or a contract wired to another
ledger cannot be connected.

Deployment order:

1. `VendorLedger` (no arguments)
2. `ClaimTribunal(vendor_ledger_address)`
3. `EscalationBoard(vendor_ledger_address)`
4. `ClaimTribunal.set_escalation_board(escalation_board_address)`
5. `EscalationBoard.set_claim_tribunal(claim_tribunal_address)`
6. `VendorLedger.set_claim_tribunal(claim_tribunal_address)`
7. `VendorLedger.set_escalation_board(escalation_board_address)`
8. `ClaimTribunal.add_vendor_source(vendor, source_url)` for each vendor

## 6. Prompt hygiene

Claimant text and page text are both marked as untrusted data in every
prompt, with an instruction to ignore instructions inside them. `<` and
`>` are replaced in the claimant's text and in the fetched evidence, so
neither can close or open the `<claim>`, `<fact>` or `<evidence>` blocks.

## 7. Known limitations (not bugs; stated so reviewers do not have to find them)

- **Relevance is still a model judgment.** Code proves an approving quote
  exists in the evidence; it cannot prove the quote supports the claim.
  Independent validators reduce this, but a mistake every model makes the
  same way is not caught.
- **Source authority rests on the owner.** Approving a document path does
  not guarantee its content. Instructions planted in an approved page,
  and server-side redirects from it, are not controlled by the contract.
- **History semantics.** One rejection with no other history puts a
  vendor in HIGH. Deliberately weak claims against a vendor raise its
  tier and make later claims against it need more agreement.
- **Asynchronous history.** The tier is read from finalized history;
  claims filed while earlier outcomes are still in flight use the older
  tier.
- **Evidence limits.** Text past 6000 characters is not seen. HTTP status
  codes are not available to the contract. A page that changes between
  fetches (timestamps, ads) makes stage 2 fail: nothing is recorded, but
  the claim cannot be filed until the page is stable.
- **Exact verdict agreement** can make borderline claims fail to reach
  consensus; nothing is recorded in that case.
- **The claim record on ClaimTribunal is not updated by an overturn.** The
  final state of an appealed claim is on EscalationBoard and in the
  ledger's claim status.

## 8. GenVM constraints relied on (confirmed live in this or earlier projects)

- Contract header is exactly two comment lines; no comment block longer
  than 3 lines, or schema loading fails with an empty `invalid_contract`.
- `raise gl.vm.UserError(...)`; `u256` for persistent integers.
- `.view()`/`.emit()` only outside nondet blocks; `.emit()` is
  asynchronous and delivered after finalization; inside a callee reached
  by `.emit()`, `gl.message.sender_address` is the calling contract.
- `gl.eq_principle.strict_eq(fn)`; `gl.vm.run_nondet_unsafe(leader_fn,
  validator_fn)` positional, validator receives `gl.vm.Return` with
  `.calldata`, and a dict survives the round trip.
- Model replies may arrive wrapped in a markdown fence.
