# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json


def _normalize_address(value) -> Address:
    if isinstance(value, Address):
        return value
    if isinstance(value, int):
        return Address(value.to_bytes(20, "big"))
    return Address(value)


def _zero_address() -> Address:
    return Address(int(0).to_bytes(20, "big"))


def _extract_json_object(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start:end + 1]
    return t


RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"

VERDICT_APPROVED = "APPROVED"
VERDICT_REJECTED = "REJECTED"


class ClaimTribunal(gl.Contract):
    owner: Address
    vendor_ledger: Address
    escalation_board: Address
    escalation_board_set: bool
    next_claim_id: u256
    claims: TreeMap[u256, str]

    def __init__(self, vendor_ledger_address):
        self.owner = gl.message.sender_address
        self.vendor_ledger = _normalize_address(vendor_ledger_address)
        self.escalation_board = _zero_address()
        self.escalation_board_set = False
        self.next_claim_id = u256(0)

    @gl.public.write
    def set_escalation_board(self, escalation_board_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set escalation board")
        if self.escalation_board_set:
            raise gl.vm.UserError("escalation board already set")
        self.escalation_board = _normalize_address(escalation_board_address)
        self.escalation_board_set = True

    @gl.public.write
    def file_claim(
        self,
        vendor,
        description: str,
        claimed_fact: str,
        evidence_url: str,
    ) -> u256:
        vendor_addr = _normalize_address(vendor)
        claimant_addr = gl.message.sender_address

        # Cross-contract .view() outside any nondet block; its result is
        # the branch condition below, verified live before relying on it.
        risk = gl.get_contract_at(self.vendor_ledger).view().get_vendor_risk(vendor_addr)

        claim_id = self.next_claim_id
        self.next_claim_id = self.next_claim_id + 1

        if risk == RISK_LOW:
            verdict = self._resolve_low(claimed_fact, evidence_url)
        elif risk == RISK_MEDIUM:
            verdict = self._resolve_medium(description, claimed_fact)
        else:
            verdict = self._resolve_high(description, claimed_fact)

        record = {
            "claim_id": int(claim_id),
            "claimant": str(claimant_addr),
            "vendor": str(vendor_addr),
            "description": description,
            "claimed_fact": claimed_fact,
            "evidence_url": evidence_url,
            "risk_tier": risk,
            "verdict": verdict,
            "escalated": False,
        }
        self.claims[claim_id] = json.dumps(record)

        approved = verdict == VERDICT_APPROVED
        # Cross-contract .emit() (fire-and-forget, async) also happens
        # outside any nondet block.
        gl.get_contract_at(self.vendor_ledger).emit().record_outcome(vendor_addr, approved)

        return claim_id

    def _resolve_low(self, claimed_fact: str, evidence_url: str) -> str:
        fact_lower = claimed_fact.strip().lower()

        def fetch_and_check() -> str:
            page = gl.nondet.web.render(evidence_url, mode="text")
            return "MATCH" if fact_lower in page.lower() else "NO_MATCH"

        result = gl.eq_principle.strict_eq(fetch_and_check)
        return VERDICT_APPROVED if result == "MATCH" else VERDICT_REJECTED

    def _resolve_medium(self, description: str, claimed_fact: str) -> str:
        def analyze() -> str:
            prompt = (
                "You are evaluating a warranty/service claim under two "
                "independent readings, then giving one final decision.\n\n"
                "Claim description:\n<claim>\n" + description + "\n</claim>\n\n"
                "Claimant's canonical fact assertion:\n<fact>\n"
                + claimed_fact + "\n</fact>\n\n"
                "Step 1 - Strict literal reading: using only the literal "
                "wording of any policy/agreement referenced above, is the "
                "claimed fact supported? Answer APPROVED or REJECTED.\n"
                "Step 2 - Reasonable-person reading: using the evident intent "
                "of any policy/agreement referenced above, is the claimed "
                "fact supported? Answer APPROVED or REJECTED.\n"
                "Step 3 - Combine: if both readings agree, use that as the "
                "final decision. If they disagree, use REJECTED (a "
                "disagreement can still be escalated).\n\n"
                "Respond with strict JSON only, no other text, no markdown "
                "fence:\n"
                '{"literal_reading": "APPROVED or REJECTED", '
                '"reasonable_reading": "APPROVED or REJECTED", '
                '"final_decision": "APPROVED or REJECTED"}'
            )
            raw = gl.nondet.exec_prompt(prompt)
            return _extract_json_object(raw)

        decision_json = gl.eq_principle.prompt_comparative(
            analyze,
            "Two-reading claim assessment must reach the same final_decision.",
        )
        try:
            parsed = json.loads(decision_json)
            verdict = parsed.get("final_decision", VERDICT_REJECTED)
        except Exception:
            verdict = VERDICT_REJECTED
        if verdict not in (VERDICT_APPROVED, VERDICT_REJECTED):
            verdict = VERDICT_REJECTED
        return verdict

    def _resolve_high(self, description: str, claimed_fact: str) -> str:
        def analyze() -> str:
            prompt = (
                "You are the lead reviewer for a warranty/service claim "
                "against a vendor with a poor history of past claim "
                "rejections. Produce a thorough written assessment before "
                "deciding, because this vendor has earned less benefit of "
                "the doubt.\n\n"
                "Claim description:\n<claim>\n" + description + "\n</claim>\n\n"
                "Claimant's canonical fact assertion:\n<fact>\n"
                + claimed_fact + "\n</fact>\n\n"
                "Analyze the claim in detail, then give a final decision.\n\n"
                "Respond with strict JSON only, no other text, no markdown "
                "fence:\n"
                '{"reasoning": "<detailed analysis, max 800 chars>", '
                '"final_decision": "APPROVED or REJECTED"}'
            )
            raw = gl.nondet.exec_prompt(prompt)
            return _extract_json_object(raw)

        decision_json = gl.eq_principle.prompt_non_comparative(
            analyze,
            task="Produce a thorough warranty-claim assessment with reasoning and a final decision.",
            criteria=(
                "The final_decision must be exactly APPROVED or REJECTED, "
                "must follow from the stated reasoning, and the reasoning "
                "must reference both the claim description and the "
                "claimant's fact assertion."
            ),
        )
        try:
            parsed = json.loads(decision_json)
            verdict = parsed.get("final_decision", VERDICT_REJECTED)
        except Exception:
            verdict = VERDICT_REJECTED
        if verdict not in (VERDICT_APPROVED, VERDICT_REJECTED):
            verdict = VERDICT_REJECTED
        return verdict

    @gl.public.write
    def request_escalation(self, claim_id: u256) -> None:
        if not self.escalation_board_set:
            raise gl.vm.UserError("escalation board not configured yet")
        if claim_id not in self.claims:
            raise gl.vm.UserError("claim not found")

        record = json.loads(self.claims[claim_id])
        if str(gl.message.sender_address) != record.get("claimant"):
            raise gl.vm.UserError("only the recorded claimant can escalate this claim")
        if record.get("escalated"):
            raise gl.vm.UserError("claim already escalated")
        if record.get("verdict") != VERDICT_REJECTED:
            raise gl.vm.UserError("only a rejected claim can be escalated")

        record["escalated"] = True
        self.claims[claim_id] = json.dumps(record)

        # Cross-contract .emit() outside any nondet block.
        gl.get_contract_at(self.escalation_board).emit().file_escalation(
            claim_id, json.dumps(record)
        )

    @gl.public.view
    def get_claim(self, claim_id: u256) -> str:
        if claim_id not in self.claims:
            raise gl.vm.UserError("claim not found")
        return self.claims[claim_id]

    @gl.public.view
    def get_claim_count(self) -> u256:
        return self.next_claim_id
