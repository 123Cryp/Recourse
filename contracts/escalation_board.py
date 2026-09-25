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


VERDICT_APPROVED = "APPROVED"
VERDICT_REJECTED = "REJECTED"


class EscalationBoard(gl.Contract):
    owner: Address
    vendor_ledger: Address
    claim_tribunal: Address
    claim_tribunal_set: bool
    next_escalation_id: u256
    escalations: TreeMap[u256, str]

    def __init__(self, vendor_ledger_address):
        self.owner = gl.message.sender_address
        self.vendor_ledger = _normalize_address(vendor_ledger_address)
        self.claim_tribunal = _zero_address()
        self.claim_tribunal_set = False
        self.next_escalation_id = u256(0)

    @gl.public.write
    def set_claim_tribunal(self, claim_tribunal_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set claim tribunal")
        if self.claim_tribunal_set:
            raise gl.vm.UserError("claim tribunal already set")
        self.claim_tribunal = _normalize_address(claim_tribunal_address)
        self.claim_tribunal_set = True

    @gl.public.write
    def file_escalation(self, claim_id: u256, claim_data: str) -> None:
        if not self.claim_tribunal_set or gl.message.sender_address != self.claim_tribunal:
            raise gl.vm.UserError("only the configured claim tribunal can file an escalation")

        original = json.loads(claim_data)
        vendor_addr = _normalize_address(original.get("vendor"))
        description = original.get("description", "")
        claimed_fact = original.get("claimed_fact", "")

        final_decision = self._review(description, claimed_fact)

        escalation_id = self.next_escalation_id
        self.next_escalation_id = self.next_escalation_id + 1
        record = {
            "escalation_id": int(escalation_id),
            "claim_id": int(claim_id),
            "vendor": str(vendor_addr),
            "original_verdict": original.get("verdict"),
            "final_decision": final_decision,
            "overturned": final_decision != original.get("verdict"),
        }
        self.escalations[escalation_id] = json.dumps(record)

        approved = final_decision == VERDICT_APPROVED
        # Cross-contract .emit() outside any nondet block; the corrected
        # outcome updates the vendor's own risk category, not the claim.
        gl.get_contract_at(self.vendor_ledger).emit().record_outcome(vendor_addr, approved)

    def _review(self, description: str, claimed_fact: str) -> str:
        def analyze() -> str:
            prompt = (
                "You are an appeals reviewer re-examining a rejected "
                "warranty/service claim under three independent framings, "
                "then giving one final decision. This review must be "
                "stricter and more skeptical than a first-pass review.\n\n"
                "Claim description:\n<claim>\n" + description + "\n</claim>\n\n"
                "Claimant's canonical fact assertion:\n<fact>\n"
                + claimed_fact + "\n</fact>\n\n"
                "Framing 1 - Strict literal reading of any policy/agreement "
                "referenced above: is the claimed fact supported? Answer "
                "APPROVED or REJECTED.\n"
                "Framing 2 - Reasonable-person/intent reading: is the "
                "claimed fact supported? Answer APPROVED or REJECTED.\n"
                "Framing 3 - Skeptical adversarial reading, actively "
                "looking for reasons the claim should fail: is the claimed "
                "fact still supported? Answer APPROVED or REJECTED.\n"
                "Step 4 - Combine: the final decision is APPROVED only if "
                "all three framings agree APPROVED; any disagreement or "
                "any REJECTED framing means the final decision is "
                "REJECTED.\n\n"
                "Respond with strict JSON only, no other text, no markdown "
                "fence:\n"
                '{"literal_reading": "APPROVED or REJECTED", '
                '"intent_reading": "APPROVED or REJECTED", '
                '"adversarial_reading": "APPROVED or REJECTED", '
                '"final_decision": "APPROVED or REJECTED"}'
            )
            raw = gl.nondet.exec_prompt(prompt)
            return _extract_json_object(raw)

        decision_json = gl.eq_principle.prompt_comparative(
            analyze,
            "Three-framing appeal assessment must reach the same final_decision.",
        )
        try:
            parsed = json.loads(decision_json)
            verdict = parsed.get("final_decision", VERDICT_REJECTED)
        except Exception:
            verdict = VERDICT_REJECTED
        if verdict not in (VERDICT_APPROVED, VERDICT_REJECTED):
            verdict = VERDICT_REJECTED
        return verdict

    @gl.public.view
    def get_escalation(self, escalation_id: u256) -> str:
        if escalation_id not in self.escalations:
            raise gl.vm.UserError("escalation not found")
        return self.escalations[escalation_id]

    @gl.public.view
    def get_escalation_count(self) -> u256:
        return self.next_escalation_id
