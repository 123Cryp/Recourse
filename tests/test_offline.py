"""
Offline tests for the Recourse contracts, using genlayer-test's Direct Mode
(in-process GenVM, no Studio/Docker needed).

IMPORTANT: the exact signature of the mock_web / mock_llm cheatcodes, and
of impersonating a specific sender address (used below via a `sender=`
kwarg to simulate a call arriving "from" a given contract address), was
NOT re-verified against the installed genlayer-test version in this
environment -- it is carried over from Tribunal's own offline suite,
which itself carried a matching warning. Run once and adjust to whatever
error message pytest reports for the actual installed version before
trusting these tests, mirroring the project's own rule of never trusting
unverified SDK surface. The live Studio results in DESIGN_DECISIONS.md
and LESSONS_LEARNED.md are the ground truth already confirmed; this file
is the offline complement (fast, repeatable, no live GenVM/LLM calls).

Run with:
    pip install genlayer-test
    pytest tests/ -v
"""

import json
import pytest


VENDOR_LEDGER_PATH = "contracts/vendor_ledger.py"
CLAIM_TRIBUNAL_PATH = "contracts/claim_tribunal.py"
ESCALATION_BOARD_PATH = "contracts/escalation_board.py"


def _wire_up(direct_deploy):
    """Deploy all three contracts and complete the 7-step authorization
    handshake, mirroring the documented deployment order in
    DESIGN_DECISIONS.md section 5."""
    ledger = direct_deploy(VENDOR_LEDGER_PATH)
    tribunal = direct_deploy(CLAIM_TRIBUNAL_PATH, ledger.address)
    board = direct_deploy(ESCALATION_BOARD_PATH, ledger.address)

    tribunal.set_escalation_board(board.address)
    board.set_claim_tribunal(tribunal.address)
    ledger.set_claim_tribunal(tribunal.address)
    ledger.set_escalation_board(board.address)

    return ledger, tribunal, board


# ---------------------------------------------------------------------------
# VendorLedger
# ---------------------------------------------------------------------------

def test_ledger_starts_low_risk_with_no_history(direct_deploy, direct_accounts):
    ledger = direct_deploy(VENDOR_LEDGER_PATH)
    vendor = direct_accounts[1]
    assert ledger.get_vendor_risk(vendor) == "LOW"


def test_ledger_rejects_unauthorized_record_outcome(direct_deploy, direct_accounts):
    ledger = direct_deploy(VENDOR_LEDGER_PATH)
    vendor = direct_accounts[1]
    # No tribunal/board configured yet -> any caller must be rejected.
    with pytest.raises(Exception):
        ledger.record_outcome(vendor, False)


def test_ledger_accepts_outcome_from_configured_claim_tribunal(direct_deploy, direct_accounts):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]
    ledger.record_outcome(vendor, False, sender=tribunal.address)
    stats = json.loads(ledger.get_vendor_stats(vendor))
    assert stats["rejected"] == 1
    assert stats["approved"] == 0


def test_ledger_rejects_outcome_from_unrelated_address(direct_deploy, direct_accounts):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]
    stranger = direct_accounts[2]
    with pytest.raises(Exception):
        ledger.record_outcome(vendor, False, sender=stranger)


def test_risk_becomes_high_after_single_rejection(direct_deploy, direct_accounts):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]
    # 1 rejection out of 1 total = 100% reject rate -> HIGH immediately,
    # matching the live Studio result (no need to accumulate many claims).
    ledger.record_outcome(vendor, False, sender=tribunal.address)
    assert ledger.get_vendor_risk(vendor) == "HIGH"


def test_risk_stays_low_after_single_approval(direct_deploy, direct_accounts):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]
    ledger.record_outcome(vendor, True, sender=tribunal.address)
    assert ledger.get_vendor_risk(vendor) == "LOW"


def test_set_claim_tribunal_only_once(direct_deploy):
    ledger = direct_deploy(VENDOR_LEDGER_PATH)
    tribunal = direct_deploy(CLAIM_TRIBUNAL_PATH, ledger.address)
    ledger.set_claim_tribunal(tribunal.address)
    with pytest.raises(Exception):
        ledger.set_claim_tribunal(tribunal.address)


def test_set_claim_tribunal_only_owner(direct_deploy, direct_accounts):
    ledger = direct_deploy(VENDOR_LEDGER_PATH)
    tribunal = direct_deploy(CLAIM_TRIBUNAL_PATH, ledger.address)
    not_owner = direct_accounts[1]
    with pytest.raises(Exception):
        ledger.set_claim_tribunal(tribunal.address, sender=not_owner)


# ---------------------------------------------------------------------------
# ClaimTribunal -- LOW tier (deterministic, no LLM needed)
# ---------------------------------------------------------------------------

def test_low_risk_approved_when_fact_matches(direct_deploy, direct_accounts, mock_web):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]

    # TODO: verify exact mock_web signature against installed genlayer-test.
    mock_web.set_page("https://example.test/evidence", "the item was delivered broken")

    claim_id = tribunal.file_claim(
        vendor,
        "Warranty claim over a broken item",
        "the item was delivered broken",
        "https://example.test/evidence",
    )
    assert claim_id == 0

    claim = json.loads(tribunal.get_claim(0))
    assert claim["risk_tier"] == "LOW"
    assert claim["verdict"] == "APPROVED"


def test_low_risk_rejected_when_fact_does_not_match(direct_deploy, direct_accounts, mock_web):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]

    mock_web.set_page("https://example.test/evidence", "the item arrived in perfect condition")

    claim_id = tribunal.file_claim(
        vendor,
        "Warranty claim over a broken item",
        "the item was delivered broken",
        "https://example.test/evidence",
    )

    claim = json.loads(tribunal.get_claim(claim_id))
    assert claim["verdict"] == "REJECTED"

    # The rejection must also propagate to VendorLedger.
    assert ledger.get_vendor_risk(vendor) == "HIGH"


# ---------------------------------------------------------------------------
# ClaimTribunal -- the core Recourse property: identical claims, different
# vendor risk, different EP paths (DESIGN_DECISIONS.md section 1)
# ---------------------------------------------------------------------------

def test_identical_claims_take_different_ep_paths_by_vendor_risk(
    direct_deploy, direct_accounts, mock_web, mock_llm
):
    ledger, tribunal, board = _wire_up(direct_deploy)
    clean_vendor = direct_accounts[1]
    bad_vendor = direct_accounts[2]

    # Seed bad_vendor with a prior rejection so its risk is HIGH before the
    # comparison claims are ever filed.
    ledger.record_outcome(bad_vendor, False, sender=tribunal.address)
    assert ledger.get_vendor_risk(bad_vendor) == "HIGH"
    assert ledger.get_vendor_risk(clean_vendor) == "LOW"

    description = "Identical test claim used to compare EP paths across vendors."
    claimed_fact = "the product arrived with a cracked screen out of the box"
    evidence_url = "https://example.test/evidence"

    # clean_vendor -> LOW risk -> strict_eq on a real (mocked) web fetch.
    mock_web.set_page(evidence_url, "the product arrived with a cracked screen out of the box")
    clean_claim_id = tribunal.file_claim(clean_vendor, description, claimed_fact, evidence_url)
    clean_claim = json.loads(tribunal.get_claim(clean_claim_id))
    assert clean_claim["risk_tier"] == "LOW"
    assert clean_claim["verdict"] == "APPROVED"

    # bad_vendor -> HIGH risk -> prompt_non_comparative, never touches
    # mock_web at all for this branch.
    mock_llm.set_response(
        json.dumps({
            "reasoning": "Unsubstantiated claim against a high-risk vendor.",
            "final_decision": "REJECTED",
        })
    )
    bad_claim_id = tribunal.file_claim(bad_vendor, description, claimed_fact, evidence_url)
    bad_claim = json.loads(tribunal.get_claim(bad_claim_id))
    assert bad_claim["risk_tier"] == "HIGH"
    assert bad_claim["verdict"] == "REJECTED"

    # Same description/fact/evidence_url, different risk tier and verdict
    # path -- this is the property that would fail if VendorLedger's
    # output were decorative rather than an actual branch condition.
    assert clean_claim["description"] == bad_claim["description"]
    assert clean_claim["claimed_fact"] == bad_claim["claimed_fact"]
    assert clean_claim["risk_tier"] != bad_claim["risk_tier"]


def test_medium_risk_uses_prompt_comparative(direct_deploy, direct_accounts, mock_llm):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]

    # 1 rejection + 1 approval = 50% reject rate -> MEDIUM under the
    # LOW_RISK_MAX_BPS=2000 / HIGH_RISK_MIN_BPS=5000 thresholds.
    ledger.record_outcome(vendor, False, sender=tribunal.address)
    ledger.record_outcome(vendor, True, sender=tribunal.address)
    assert ledger.get_vendor_risk(vendor) == "MEDIUM"

    mock_llm.set_response(
        json.dumps({
            "literal_reading": "APPROVED",
            "reasonable_reading": "APPROVED",
            "final_decision": "APPROVED",
        })
    )

    claim_id = tribunal.file_claim(
        vendor,
        "A medium-risk warranty claim",
        "the unit failed within the warranty period",
        "https://example.test/evidence",
    )
    claim = json.loads(tribunal.get_claim(claim_id))
    assert claim["risk_tier"] == "MEDIUM"
    assert claim["verdict"] == "APPROVED"


# ---------------------------------------------------------------------------
# ClaimTribunal -- request_escalation access control
# ---------------------------------------------------------------------------

def test_request_escalation_only_by_recorded_claimant(direct_deploy, direct_accounts, mock_web):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]
    claimant = direct_accounts[0]
    stranger = direct_accounts[2]

    mock_web.set_page("https://example.test/evidence", "no match here")
    claim_id = tribunal.file_claim(
        vendor, "desc", "the fact that is not on the page", "https://example.test/evidence",
        sender=claimant,
    )

    with pytest.raises(Exception):
        tribunal.request_escalation(claim_id, sender=stranger)


def test_request_escalation_only_for_rejected_claim(direct_deploy, direct_accounts, mock_web):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]
    claimant = direct_accounts[0]

    mock_web.set_page("https://example.test/evidence", "the exact matching fact")
    claim_id = tribunal.file_claim(
        vendor, "desc", "the exact matching fact", "https://example.test/evidence",
        sender=claimant,
    )
    claim = json.loads(tribunal.get_claim(claim_id))
    assert claim["verdict"] == "APPROVED"

    with pytest.raises(Exception):
        tribunal.request_escalation(claim_id, sender=claimant)


def test_request_escalation_only_once(direct_deploy, direct_accounts, mock_web):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]
    claimant = direct_accounts[0]

    mock_web.set_page("https://example.test/evidence", "no match here")
    claim_id = tribunal.file_claim(
        vendor, "desc", "the fact that is not on the page", "https://example.test/evidence",
        sender=claimant,
    )

    tribunal.request_escalation(claim_id, sender=claimant)
    with pytest.raises(Exception):
        tribunal.request_escalation(claim_id, sender=claimant)


def test_set_escalation_board_only_once(direct_deploy):
    ledger = direct_deploy(VENDOR_LEDGER_PATH)
    tribunal = direct_deploy(CLAIM_TRIBUNAL_PATH, ledger.address)
    board = direct_deploy(ESCALATION_BOARD_PATH, ledger.address)

    tribunal.set_escalation_board(board.address)
    with pytest.raises(Exception):
        tribunal.set_escalation_board(board.address)


# ---------------------------------------------------------------------------
# EscalationBoard
# ---------------------------------------------------------------------------

def test_file_escalation_rejects_calls_not_from_claim_tribunal(direct_deploy, direct_accounts):
    ledger, tribunal, board = _wire_up(direct_deploy)
    stranger = direct_accounts[2]
    vendor = direct_accounts[1]

    fake_claim_json = json.dumps({
        "claim_id": 0,
        "vendor": str(vendor),
        "description": "Forged claim",
        "claimed_fact": "forged fact",
        "verdict": "REJECTED",
    })
    with pytest.raises(Exception):
        board.file_escalation(0, fake_claim_json, sender=stranger)


def test_escalation_overturns_and_updates_vendor_ledger(direct_deploy, direct_accounts, mock_llm):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]

    # Seed one prior rejection so the vendor already carries a HIGH risk
    # record before the escalation is filed.
    ledger.record_outcome(vendor, False, sender=tribunal.address)

    claim_json = json.dumps({
        "claim_id": 0,
        "vendor": str(vendor),
        "description": "Warranty claim under appeal",
        "claimed_fact": "the unit was defective on arrival",
        "verdict": "REJECTED",
    })

    mock_llm.set_response(
        json.dumps({
            "literal_reading": "APPROVED",
            "intent_reading": "APPROVED",
            "adversarial_reading": "APPROVED",
            "final_decision": "APPROVED",
        })
    )

    board.file_escalation(0, claim_json, sender=tribunal.address)

    escalation = json.loads(board.get_escalation(0))
    assert escalation["overturned"] is True
    assert escalation["final_decision"] == "APPROVED"

    # The escalation's corrected outcome must land in VendorLedger, not
    # back in ClaimTribunal -- DESIGN_DECISIONS.md section 2.
    stats = json.loads(ledger.get_vendor_stats(vendor))
    assert stats["approved"] == 1
    assert stats["rejected"] == 1


def test_escalation_confirms_when_decision_unchanged(direct_deploy, direct_accounts, mock_llm):
    ledger, tribunal, board = _wire_up(direct_deploy)
    vendor = direct_accounts[1]

    claim_json = json.dumps({
        "claim_id": 0,
        "vendor": str(vendor),
        "description": "A claim that should be confirmed on appeal",
        "claimed_fact": "unsupported assertion",
        "verdict": "REJECTED",
    })

    mock_llm.set_response(
        json.dumps({
            "literal_reading": "REJECTED",
            "intent_reading": "REJECTED",
            "adversarial_reading": "REJECTED",
            "final_decision": "REJECTED",
        })
    )

    board.file_escalation(0, claim_json, sender=tribunal.address)

    escalation = json.loads(board.get_escalation(0))
    assert escalation["overturned"] is False
    assert escalation["final_decision"] == "REJECTED"
