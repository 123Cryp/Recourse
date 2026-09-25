# Recourse — Design Decisions

## 0. Context and goal

Fourth project after AccreditationCheck (accepted, 100 points), Covenant
(three independent escrows, EP varying by evidence type), and Tribunal
(three-contract adjudication pipeline with real precedent memory, deployed
live, and corrected after a steward flagged missing cross-contract access
control on `record_verdict`/`file_appeal`/`request_appeal`). Target: 500–600
points in the Intelligent Contracts category.

Tribunal established that **consensus strictness should rescale with the
importance of what is being judged**, using claimed amount as the scaling
axis. Recourse deliberately does not reuse that axis or Tribunal's
first-instance/appeal/precedent architecture. It explores a different
dimension of the same principle: strictness driven by a **third party's
track record**, not by the size of the dispute or the credibility of either
party actually before the tribunal.

## 1. The new mechanism (before writing a single line of code)

**A warranty/service-claim adjudication system whose consensus strictness
is set by the counterparty's (vendor's) own claim-outcome history — not by
the claimant's credibility and not by the dollar amount in dispute.** The
entity that determines how hard the system looks is not a participant in
the current dispute in any active sense; it is a passive record of how that
vendor has fared on unrelated past claims.

Why this is a genuinely different mechanism from Tribunal's, not a
relabeled copy:

- Tribunal's strictness axis is a **number chosen by the claimant at filing
  time** (`claimed_amount`) — a claimant could in principle inflate or
  deflate it, and it says nothing about either party's track record.
  Recourse's axis is a **derived statistic about a party who takes no
  action in the current transaction at all** (the vendor doesn't file
  anything or respond to anything to produce this number — it is computed
  entirely from that vendor's accumulated history of *other* claims).
- Tribunal's memory (PrecedentRegistry) is consulted as *contextual input*
  to a prompt but never changes *which* equivalence principle runs — the EP
  is selected purely by `claimed_amount` regardless of precedent content.
  In Recourse, the ledger's output (a risk category) directly selects the
  EP branch itself, not just the prompt content. This is the one property
  a test can directly assert: two structurally identical claims against a
  clean-history vendor and a bad-history vendor must take two different EP
  code paths, not just receive different prompt text.
- The result of each claim feeds back into the very same number that
  produced it (approve/reject outcome → updates the vendor's reject rate →
  changes the strictness applied to that vendor's *next* claim). This
  closes a feedback loop Tribunal does not have: Tribunal's PrecedentRegistry
  never influences its own future admission criteria, only future prompt
  content.

A test that would fail if this were fake/decorative: file two claims with
byte-identical `description`/`claimed_fact`/`evidence_url` against two
different vendor addresses — one seeded in VendorLedger with a clean/empty
history, one seeded with a high reject rate — and assert (a) the two claims
resolve via different equivalence-principle functions (observable via which
internal `_resolve_*` path ran / which EP call was made, not just the final
verdict string), and (b) if VendorLedger is instead seeded so both vendors
report the same risk category, the two claims take the *same* path despite
being filed against nominally "different" vendors — proving the branch is
driven by the ledger's output and nothing else.

## 2. The three contracts and the justification for each equivalence principle

### VendorLedger — no EP of its own, the system's connective tissue
**What it does:** an append-only per-vendor history of claim outcomes.
Exposes a real `.view()` (`get_vendor_risk`) that computes a reject rate
from the vendor's counters and returns a deterministic risk category
(`LOW` / `MEDIUM` / `HIGH`), and a real `.emit()` (`record_outcome`) that
appends a new outcome and updates the counters. Architecturally identical
in role to Tribunal's PrecedentRegistry: pure arithmetic on integers, no
LLM judgment, no EP.
**Why no EP:** the reject-rate → risk-category mapping is a fixed,
auditable threshold function (e.g. no history or reject rate below a low
threshold → `LOW`; between low and high thresholds → `MEDIUM`; at or above
the high threshold → `HIGH`). Giving this an LLM-backed EP would be exactly
the "hello-world with a decorative EP" pattern the portal penalizes — its
value is structural (it is what the other two contracts actually read from
and write to), not computational.

### ClaimTribunal — strictness selected by vendor risk category (three paths, three EPs)
**What it does:** a claimant files a warranty/service claim against a
vendor with a description, a canonical claimed fact, and an evidence URL.
Before selecting an equivalence principle, it makes a real `.view()` call
to `VendorLedger.get_vendor_risk(vendor)`:
- **LOW risk (clean history / no history):** `strict_eq` on a real web
  fetch of the evidence URL — checking whether the canonical fact
  literally appears. A vendor with no adverse record gets the cheapest,
  least adversarial check.
- **MEDIUM risk:** `prompt_comparative` — two independent readings that
  must agree on a final verdict.
- **HIGH risk (vendor previously rejected often — "suspicious"):**
  `prompt_non_comparative` — the most thorough, multi-step review (a single
  detailed leader analysis, audited by validators against explicit
  criteria). A vendor with a track record of rejected claims is placed
  under the most scrutiny, deliberately the opposite of "benefit of the
  doubt."
After the verdict, `ClaimTribunal` makes a real `.emit()` call to
`VendorLedger.record_outcome(vendor, approved_or_rejected)` so the vendor's
risk category can shift for the *next* claim filed against them.
**Why the axis is the vendor's history and not the claimant's or the
amount:** the honest framing of a warranty system is that a vendor who has
stonewalled claimants repeatedly in the past has earned less benefit of
the doubt going forward; a claimant's own credibility or the dollar amount
at stake says nothing about whether *this specific vendor* has a pattern
of bad-faith denials. This is also the property that makes the mechanism
non-gameable by the claimant: nothing the claimant does at filing time
(how they word the claim, how large a claim they file) changes which EP
runs — only the vendor's own accumulated history does.

### EscalationBoard — stricter re-review on appeal, writes back to VendorLedger (not ClaimTribunal)
**What it does:** if a claimant disputes a rejection, `EscalationBoard`
re-reviews under stricter criteria than any of ClaimTribunal's three paths:
three independent framings compared via `prompt_comparative` (more
framings than ClaimTribunal's MEDIUM path, and comparative rather than a
single audited leader, so the appeal is a genuine independent re-check, not
a rubber stamp of the same evaluator structure). It can uphold or overturn
the original rejection.
**Why it writes to `VendorLedger` and not back to `ClaimTribunal`:** the
system's state of record for "how has this vendor behaved" is
`VendorLedger`, not the individual claim record. If `EscalationBoard`
overturned into `ClaimTribunal` instead, `ClaimTribunal` would need to
special-case escalated outcomes and `VendorLedger` would silently miss the
corrected outcome — the vendor's risk category would stay wrong even after
a successful appeal. Writing the final (possibly overturned) outcome
directly to `VendorLedger` keeps the feedback loop (§1) consistent
regardless of whether a claim was resolved at first instance or on appeal.
**Why `prompt_comparative` and not `prompt_non_comparative`:** an appeal
needs genuinely independent judgments that are checked against each other
for agreement — a leader/validator audit structure (non-comparative) would
just repeat ClaimTribunal's own HIGH-risk structure and add nothing beyond
"the same kind of check, run again."

## 3. What is deliberately NOT built

Per the portal's exclusion list: no thin LLM wrapper, no purely-formatting
validator, no storage hello-world. `VendorLedger` is simple but its role is
explicitly justified in §2, not added to hit a contract count. No bonded
appeals/GEN-escrow layer is added to Recourse — that mechanism already
exists in Tribunal (`request_appeal` with a bond) and Covenant; repeating it
here would dilute the one genuinely new idea (history-driven strictness)
this project exists to demonstrate. Appeal cost-of-entry, if wanted, is
documented as an explicit v2 idea, not silently reused from Tribunal.

## 4. Confirmed GenVM constraints inherited from AccreditationCheck, Covenant, and Tribunal (not rediscovered)

- Contract file header must be **exactly two comment lines**, nothing more,
  and no comment block longer than 3–4 consecutive lines anywhere else in
  the file — either triggers `VM_ERROR: invalid_contract` with completely
  empty stdout/stderr (live-confirmed on Tribunal, hours of debugging via
  file-bisection). Detailed rationale belongs in this document, not in code
  comments:
  ```
  # v0.1.0
  # { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
  ```
- Always `raise gl.vm.UserError(...)` — bare `UserError` is not re-exported
  by `from genlayer import *`.
- `gl.vm.run_nondet(leader_fn, validator_fn)` positional-only, no keywords.
- Every validator must call `gl.vm.unpack_result()` on the leader's result
  before using it.
- Constructor address arguments must be normalized — they may arrive as a
  raw `int` or `str`, not only `Address`.
- Raw `int` is not supported for persistent fields — use `u256`/`i32`/etc.
- An LLM's JSON output may arrive wrapped in a ` ```json ... ``` ` fence;
  strip it before `json.loads`.
- `gl.eq_principle.prompt_comparative(fn, principle)` — the second argument
  is positional and named `principle`, not a `task=` keyword (the
  documentation is wrong here; this signature was confirmed by a live
  error).
- `gl.eq_principle.prompt_non_comparative(fn, *, task, criteria)` — this
  one genuinely does take `task`/`criteria` as keywords; its documentation
  was correct.
- `gl.get_contract_at(...).view()` / `.emit()` must always be called
  **outside** any `run_nondet`/`eq_principle` block. Calling either from
  inside a `leader_fn`/`validator_fn` raises `SystemError: 6: forbidden` —
  a VM-level trap, not catchable with `try/except`.
- `.emit()` (with arguments, or `emit_transfer`) is asynchronous — an
  immediate same-transaction `.view()` on the written-to contract still
  returns the pre-write value. No logic anywhere in Recourse assumes
  read-after-write within one transaction.
- `gl.message.sender_address` inside a contract reached via `.emit()` is
  the address of the **calling contract**, not the original human sender.
  Any contract that needs the original human sender's address must receive
  it explicitly as part of the passed data.
- `gl.message.value` is returned with 18 decimals (wei-like).
- Time source: `https://www.cloudflare.com/cdn-cgi/trace` (the `ts=`
  line) — `worldtimeapi.org` is permanently sunset (HTTP 410).
- GEN transfer from within a contract:
  `gl.get_contract_at(addr).emit_transfer(value=amount)`.

## 5. Access control — designed in from the start, not patched after a steward finding

This is the single most important carried-forward lesson: Tribunal's first
submission was flagged by a steward because `record_verdict`, `file_appeal`,
and `request_appeal` had no caller authorization — any address could call
them directly. Recourse's three cross-contract write entry points are
access-controlled from the first line of code, not added later:

- `VendorLedger.record_outcome` accepts calls **only** from the configured
  `claim_tribunal` or `escalation_board` addresses — checked against
  `gl.message.sender_address`, otherwise `raise gl.vm.UserError(...)`.
- `EscalationBoard.file_escalation` accepts calls **only** from the
  configured `claim_tribunal` address.
- `ClaimTribunal.request_escalation` accepts calls **only** from the
  claimant address recorded on that specific claim, and only once per
  claim (a `escalated: bool` flag on the claim record, checked and set
  before the cross-contract call, mirroring Tribunal's `appealed` flag on
  `FirstInstanceCourt.request_appeal`).

**Deploy-time chicken-and-egg problem:** the three contracts deploy in
sequence and do not have each other's addresses available at construction
time (`VendorLedger` first, with no constructor arguments; then
`ClaimTribunal`, constructed with `VendorLedger`'s address; then
`EscalationBoard`, also constructed with `VendorLedger`'s address). Each
contract therefore has an `owner` (set to `gl.message.sender_address` in
`__init__`) plus one or more owner-only, **set-once** wiring methods,
following the exact pattern already live-verified on Tribunal's
`PrecedentRegistry.set_first_instance_court` /
`set_appeals_court`:

```
set_claim_tribunal(address)      # on VendorLedger and EscalationBoard
set_escalation_board(address)    # on VendorLedger and ClaimTribunal
```

Each checks `gl.message.sender_address == self.owner` and a
`*_set: bool` guard so it can only be called once.

**Full deploy sequence (documented here before any code exists, not
reconstructed after a steward comment):**

1. Deploy `VendorLedger` (no constructor arguments).
2. Deploy `ClaimTribunal(vendor_ledger_address)`.
3. Deploy `EscalationBoard(vendor_ledger_address)`.
4. Call `ClaimTribunal.set_escalation_board(escalation_board_address)`.
5. Call `EscalationBoard.set_claim_tribunal(claim_tribunal_address)`.
6. Call `VendorLedger.set_claim_tribunal(claim_tribunal_address)`.
7. Call `VendorLedger.set_escalation_board(escalation_board_address)`.

Only after all seven steps complete is the system fully wired and safe to
use; the offline test suite includes negative tests asserting that each of
the three protected methods rejects a call from an arbitrary/unconfigured
address, and that each wiring method rejects a second call.

## 6. What still needs live verification before relying on it

Recourse reuses `.view()`/`.emit()`/`.emit(value=...)` cross-contract
patterns already live-verified on Tribunal and MatchGuard/Vigil, so no new
diagnostic contract is expected to be needed for those primitives
themselves. The one genuinely new interaction in Recourse is a `.view()`
call whose **return value is consumed as a branch condition selecting
which of three EP functions to call**, rather than only being interpolated
into a prompt string (Tribunal's `search_precedents` result is always just
prompt context, never a branch condition). Per the standing rule of
verifying every first-time cross-contract interaction live before relying
on it in the main contracts, a small diagnostic pair (a trivial
`view`-only "risk source" contract plus a caller that branches on its
return value) will be deployed and exercised on Studio before this pattern
is used inside `ClaimTribunal.file_claim`, specifically to confirm:

1. The exact Python type `.view()` returns a plain string/enum-like value
   as (not wrapped in anything requiring `unpack_result`-style unwrapping —
   expected based on Tribunal, but not yet confirmed for a value used in an
   `if`/`elif` branch rather than passed straight into a prompt).
2. That branching in plain (non-nondet) code on a `.view()` result, then
   calling a *different* `eq_principle.*` function per branch, works
   exactly as it does when the branch is on a constructor/argument value
   (as in Tribunal's `claimed_amount` tiering) — i.e. that deriving the
   branch from cross-contract data rather than transaction-local data
   introduces no new restriction.

Findings will be recorded in `LESSONS_LEARNED.md` regardless of outcome.

**Live results (`RiskSource` + `RiskBranchCaller`, deployed and exercised on
Studio):** all three branches confirmed working exactly as expected, no new
restriction introduced by deriving the branch from cross-contract data:
1. `.view()` returns a plain `str` usable directly in an `if`/`elif`
   comparison — no `unpack_result`-style unwrapping needed.
2. `LOW` branch → `strict_eq` → `SUCCESS`, `"low_path:OK"`.
3. `MEDIUM` branch → `prompt_comparative` → `SUCCESS`, `"medium_path:OK"`.
4. `HIGH` branch → `prompt_non_comparative` → `SUCCESS`, `"high_path:OK"`.
All three ran as plain (non-nondet) `@gl.public.write` calls with the
`.view()` call preceding any `eq_principle`/`nondet` block, consistent with
§4's forbidden-inside-nondet rule.

## 7. Status

Design finalized. All three contracts written with access control present
from the first draft (§5). Live Studio diagnostic from §6 completed and
confirmed. Next: deploy the three main contracts in the §5 sequence and run
the live end-to-end test (two identical claims against a clean-history
vendor and a high-reject-history vendor taking two different EP paths, per
§1), then the offline `pytest` suite with `genlayer-test` (including the
access-control negative tests from §5), then `LESSONS_LEARNED.md`, then
GitHub, then portal submission.
