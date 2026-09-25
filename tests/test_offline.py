"""
Offline tests for the Recourse contracts (VendorLedger, ClaimTribunal,
EscalationBoard), using the project's own genlayer SDK stub instead of
genlayer-test's Direct Mode -- see tests/genlayer_stub/ and
tests/_bootstrap.py. gl.nondet.web.render / gl.nondet.exec_prompt are
mocked per test to simulate specific evidence pages / LLM responses;
every other step (risk lookup, EP-path selection, access control,
cross-contract writes) runs for real through the actual contract code.

Run with:
    python3 -m unittest discover -s tests -p "test_*.py" -v
(pytest also auto-discovers these, since they're plain unittest.TestCase
classes, if you have it installed.)
"""
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    BAD_VENDOR_ADDRESS,
    CLAIMANT_ADDRESS,
    CLEAN_VENDOR_ADDRESS,
    DEPLOYER_ADDRESS,
    STRANGER_ADDRESS,
    Address,
    ClaimTribunal,
    EscalationBoard,
    VendorLedger,
    deploy,
    gl,
    set_caller,
    wire_up,
)

EVIDENCE_URL = "https://example.test/evidence"


def mock_web(content):
    return patch.object(gl.nondet.web, "render", side_effect=lambda url, mode="text": content)


def mock_llm(response):
    return patch.object(gl.nondet, "exec_prompt", side_effect=lambda p, response_format="text": response)


# ---------------------------------------------------------------------------
# VendorLedger
# ---------------------------------------------------------------------------

class VendorLedgerTests(unittest.TestCase):
    def test_starts_low_risk_with_no_history(self):
        ledger = deploy(VendorLedger, "0x" + "09" * 20)
        self.assertEqual(ledger.get_vendor_risk(Address(CLEAN_VENDOR_ADDRESS)), "LOW")

    def test_rejects_unauthorized_record_outcome(self):
        ledger = deploy(VendorLedger, "0x" + "09" * 20)
        # No tribunal/board configured yet -> any caller must be rejected.
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            ledger.record_outcome(Address(CLEAN_VENDOR_ADDRESS), False)

    def test_accepts_outcome_from_configured_claim_tribunal(self):
        ledger, tribunal, board = wire_up()
        set_caller(tribunal.address)
        ledger.record_outcome(Address(CLEAN_VENDOR_ADDRESS), False)
        stats = json.loads(ledger.get_vendor_stats(Address(CLEAN_VENDOR_ADDRESS)))
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["approved"], 0)

    def test_rejects_outcome_from_unrelated_address(self):
        ledger, tribunal, board = wire_up()
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            ledger.record_outcome(Address(CLEAN_VENDOR_ADDRESS), False)

    def test_risk_becomes_high_after_single_rejection(self):
        ledger, tribunal, board = wire_up()
        set_caller(tribunal.address)
        # 1 rejection / 1 total = 100% reject rate -> HIGH immediately,
        # matching the live Studio result (no need for many claims).
        ledger.record_outcome(Address(BAD_VENDOR_ADDRESS), False)
        self.assertEqual(ledger.get_vendor_risk(Address(BAD_VENDOR_ADDRESS)), "HIGH")

    def test_risk_stays_low_after_single_approval(self):
        ledger, tribunal, board = wire_up()
        set_caller(tribunal.address)
        ledger.record_outcome(Address(CLEAN_VENDOR_ADDRESS), True)
        self.assertEqual(ledger.get_vendor_risk(Address(CLEAN_VENDOR_ADDRESS)), "LOW")

    def test_set_claim_tribunal_only_once(self):
        ledger = deploy(VendorLedger, "0x" + "09" * 20)
        tribunal = deploy(ClaimTribunal, "0x" + "0a" * 20, Address("0x" + "09" * 20))
        set_caller(DEPLOYER_ADDRESS)
        ledger.set_claim_tribunal(tribunal.address)
        with self.assertRaises(gl.vm.UserError):
            ledger.set_claim_tribunal(tribunal.address)

    def test_set_claim_tribunal_only_owner(self):
        ledger = deploy(VendorLedger, "0x" + "09" * 20)
        tribunal = deploy(ClaimTribunal, "0x" + "0a" * 20, Address("0x" + "09" * 20))
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            ledger.set_claim_tribunal(tribunal.address)


# ---------------------------------------------------------------------------
# ClaimTribunal -- LOW tier (deterministic, no LLM needed)
# ---------------------------------------------------------------------------

class ClaimTribunalLowTierTests(unittest.TestCase):
    def test_approved_when_fact_matches(self):
        ledger, tribunal, board = wire_up()
        set_caller(CLAIMANT_ADDRESS)
        with mock_web("the item was delivered broken"):
            claim_id = tribunal.file_claim(
                Address(CLEAN_VENDOR_ADDRESS),
                "Warranty claim over a broken item",
                "the item was delivered broken",
                EVIDENCE_URL,
            )
        claim = json.loads(tribunal.get_claim(claim_id))
        self.assertEqual(claim["risk_tier"], "LOW")
        self.assertEqual(claim["verdict"], "APPROVED")

    def test_rejected_when_fact_does_not_match(self):
        ledger, tribunal, board = wire_up()
        set_caller(CLAIMANT_ADDRESS)
        with mock_web("the item arrived in perfect condition"):
            claim_id = tribunal.file_claim(
                Address(CLEAN_VENDOR_ADDRESS),
                "Warranty claim over a broken item",
                "the item was delivered broken",
                EVIDENCE_URL,
            )
        claim = json.loads(tribunal.get_claim(claim_id))
        self.assertEqual(claim["verdict"], "REJECTED")
        # The rejection must propagate to VendorLedger.
        self.assertEqual(ledger.get_vendor_risk(Address(CLEAN_VENDOR_ADDRESS)), "HIGH")


# ---------------------------------------------------------------------------
# The core Recourse property: identical claims, different vendor risk,
# different EP paths (DESIGN_DECISIONS.md section 1)
# ---------------------------------------------------------------------------

class IdenticalClaimsDifferentPathsTests(unittest.TestCase):
    def test_clean_vendor_uses_strict_eq_bad_vendor_uses_prompt_non_comparative(self):
        ledger, tribunal, board = wire_up()

        # Seed BAD_VENDOR_ADDRESS with a prior rejection so its risk is
        # HIGH before the comparison claims below are ever filed.
        set_caller(tribunal.address)
        ledger.record_outcome(Address(BAD_VENDOR_ADDRESS), False)
        self.assertEqual(ledger.get_vendor_risk(Address(BAD_VENDOR_ADDRESS)), "HIGH")
        self.assertEqual(ledger.get_vendor_risk(Address(CLEAN_VENDOR_ADDRESS)), "LOW")

        description = "Identical test claim used to compare EP paths across vendors."
        claimed_fact = "the product arrived with a cracked screen out of the box"

        # clean_vendor -> LOW risk -> strict_eq on a real (mocked) web fetch.
        set_caller(CLAIMANT_ADDRESS)
        with mock_web(claimed_fact):
            clean_claim_id = tribunal.file_claim(
                Address(CLEAN_VENDOR_ADDRESS), description, claimed_fact, EVIDENCE_URL
            )
        clean_claim = json.loads(tribunal.get_claim(clean_claim_id))
        self.assertEqual(clean_claim["risk_tier"], "LOW")
        self.assertEqual(clean_claim["verdict"], "APPROVED")

        # bad_vendor -> HIGH risk -> prompt_non_comparative, never
        # touches gl.nondet.web at all for this branch.
        set_caller(CLAIMANT_ADDRESS)
        with mock_llm(json.dumps({
            "reasoning": "Unsubstantiated claim against a high-risk vendor.",
            "final_decision": "REJECTED",
        })):
            bad_claim_id = tribunal.file_claim(
                Address(BAD_VENDOR_ADDRESS), description, claimed_fact, EVIDENCE_URL
            )
        bad_claim = json.loads(tribunal.get_claim(bad_claim_id))
        self.assertEqual(bad_claim["risk_tier"], "HIGH")
        self.assertEqual(bad_claim["verdict"], "REJECTED")

        # Same description/fact/evidence_url, different risk tier and
        # verdict path -- the property that would fail if VendorLedger's
        # output were decorative rather than an actual branch condition.
        self.assertEqual(clean_claim["description"], bad_claim["description"])
        self.assertEqual(clean_claim["claimed_fact"], bad_claim["claimed_fact"])
        self.assertNotEqual(clean_claim["risk_tier"], bad_claim["risk_tier"])

    def test_medium_risk_uses_prompt_comparative(self):
        ledger, tribunal, board = wire_up()
        vendor = Address("0x" + "66" * 20)

        # 1 rejection + 2 approvals = 33% reject rate -> MEDIUM (between
        # the LOW_RISK_MAX_BPS=2000 and HIGH_RISK_MIN_BPS=5000 thresholds;
        # note 50% itself would land in HIGH, not MEDIUM, since the HIGH
        # threshold is "at or above").
        set_caller(tribunal.address)
        ledger.record_outcome(vendor, False)
        ledger.record_outcome(vendor, True)
        ledger.record_outcome(vendor, True)
        self.assertEqual(ledger.get_vendor_risk(vendor), "MEDIUM")

        set_caller(CLAIMANT_ADDRESS)
        with mock_llm(json.dumps({
            "literal_reading": "APPROVED",
            "reasonable_reading": "APPROVED",
            "final_decision": "APPROVED",
        })):
            claim_id = tribunal.file_claim(
                vendor,
                "A medium-risk warranty claim",
                "the unit failed within the warranty period",
                EVIDENCE_URL,
            )
        claim = json.loads(tribunal.get_claim(claim_id))
        self.assertEqual(claim["risk_tier"], "MEDIUM")
        self.assertEqual(claim["verdict"], "APPROVED")


# ---------------------------------------------------------------------------
# ClaimTribunal.request_escalation access control
# ---------------------------------------------------------------------------

class RequestEscalationTests(unittest.TestCase):
    def _file_rejected_claim(self, ledger, tribunal, claimant=CLAIMANT_ADDRESS):
        set_caller(claimant)
        with mock_web("no match on this page at all"):
            return tribunal.file_claim(
                Address(CLEAN_VENDOR_ADDRESS),
                "desc",
                "the fact that is not on the page",
                EVIDENCE_URL,
            )

    def test_only_by_recorded_claimant(self):
        ledger, tribunal, board = wire_up()
        claim_id = self._file_rejected_claim(ledger, tribunal)
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            tribunal.request_escalation(claim_id)

    def test_only_for_rejected_claim(self):
        ledger, tribunal, board = wire_up()
        set_caller(CLAIMANT_ADDRESS)
        with mock_web("the exact matching fact"):
            claim_id = tribunal.file_claim(
                Address(CLEAN_VENDOR_ADDRESS), "desc", "the exact matching fact", EVIDENCE_URL
            )
        claim = json.loads(tribunal.get_claim(claim_id))
        self.assertEqual(claim["verdict"], "APPROVED")
        set_caller(CLAIMANT_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            tribunal.request_escalation(claim_id)

    def test_only_once(self):
        ledger, tribunal, board = wire_up()
        claim_id = self._file_rejected_claim(ledger, tribunal)
        set_caller(CLAIMANT_ADDRESS)
        with mock_llm(json.dumps({
            "literal_reading": "REJECTED",
            "intent_reading": "REJECTED",
            "adversarial_reading": "REJECTED",
            "final_decision": "REJECTED",
        })):
            tribunal.request_escalation(claim_id)
        set_caller(CLAIMANT_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            tribunal.request_escalation(claim_id)

    def test_set_escalation_board_only_once(self):
        ledger = deploy(VendorLedger, "0x" + "09" * 20)
        tribunal = deploy(ClaimTribunal, "0x" + "0a" * 20, Address("0x" + "09" * 20))
        board = deploy(EscalationBoard, "0x" + "0b" * 20, Address("0x" + "09" * 20))
        set_caller(DEPLOYER_ADDRESS)
        tribunal.set_escalation_board(board.address)
        with self.assertRaises(gl.vm.UserError):
            tribunal.set_escalation_board(board.address)


# ---------------------------------------------------------------------------
# EscalationBoard
# ---------------------------------------------------------------------------

class EscalationBoardTests(unittest.TestCase):
    def test_file_escalation_rejects_calls_not_from_claim_tribunal(self):
        ledger, tribunal, board = wire_up()
        fake_claim_json = json.dumps({
            "claim_id": 0,
            "vendor": CLEAN_VENDOR_ADDRESS,
            "description": "Forged claim",
            "claimed_fact": "forged fact",
            "verdict": "REJECTED",
        })
        set_caller(STRANGER_ADDRESS)
        with self.assertRaises(gl.vm.UserError):
            board.file_escalation(0, fake_claim_json)

    def test_escalation_overturns_and_updates_vendor_ledger_not_tribunal(self):
        ledger, tribunal, board = wire_up()
        vendor = Address(BAD_VENDOR_ADDRESS)

        # Seed one prior rejection so the vendor already carries a HIGH
        # risk record before the escalation is filed.
        set_caller(tribunal.address)
        ledger.record_outcome(vendor, False)

        claim_json = json.dumps({
            "claim_id": 0,
            "vendor": str(vendor),
            "description": "Warranty claim under appeal",
            "claimed_fact": "the unit was defective on arrival",
            "verdict": "REJECTED",
        })

        set_caller(tribunal.address)
        with mock_llm(json.dumps({
            "literal_reading": "APPROVED",
            "intent_reading": "APPROVED",
            "adversarial_reading": "APPROVED",
            "final_decision": "APPROVED",
        })):
            board.file_escalation(0, claim_json)

        escalation = json.loads(board.get_escalation(0))
        self.assertTrue(escalation["overturned"])
        self.assertEqual(escalation["final_decision"], "APPROVED")

        # The corrected outcome must land in VendorLedger, not back in
        # ClaimTribunal -- DESIGN_DECISIONS.md section 2.
        stats = json.loads(ledger.get_vendor_stats(vendor))
        self.assertEqual(stats["approved"], 1)
        self.assertEqual(stats["rejected"], 1)

    def test_escalation_confirms_when_decision_unchanged(self):
        ledger, tribunal, board = wire_up()
        vendor = Address(CLEAN_VENDOR_ADDRESS)
        claim_json = json.dumps({
            "claim_id": 0,
            "vendor": str(vendor),
            "description": "A claim that should be confirmed on appeal",
            "claimed_fact": "unsupported assertion",
            "verdict": "REJECTED",
        })
        set_caller(tribunal.address)
        with mock_llm(json.dumps({
            "literal_reading": "REJECTED",
            "intent_reading": "REJECTED",
            "adversarial_reading": "REJECTED",
            "final_decision": "REJECTED",
        })):
            board.file_escalation(0, claim_json)
        escalation = json.loads(board.get_escalation(0))
        self.assertFalse(escalation["overturned"])
        self.assertEqual(escalation["final_decision"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
