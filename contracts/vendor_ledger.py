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


RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"

# Reject-rate is tracked in basis points (0-10000). No history at all is
# treated as LOW risk, same as a clean history.
LOW_RISK_MAX_BPS = 2000
HIGH_RISK_MIN_BPS = 5000

STATUS_APPROVED = "A"
STATUS_REJECTED = "R"
STATUS_OVERTURNED = "O"
STATUS_PENDING_OVERTURN = "P"


def _risk_category(approved: int, rejected: int) -> str:
    total = approved + rejected
    if total == 0:
        return RISK_LOW
    reject_bps = (rejected * 10000) // total
    if reject_bps < LOW_RISK_MAX_BPS:
        return RISK_LOW
    if reject_bps < HIGH_RISK_MIN_BPS:
        return RISK_MEDIUM
    return RISK_HIGH


class VendorLedger(gl.Contract):
    owner: Address
    claim_tribunal: Address
    claim_tribunal_set: bool
    escalation_board: Address
    escalation_board_set: bool
    approved_counts: TreeMap[str, u256]
    rejected_counts: TreeMap[str, u256]
    claim_status: TreeMap[str, str]

    def __init__(self):
        self.owner = gl.message.sender_address
        self.claim_tribunal = _zero_address()
        self.claim_tribunal_set = False
        self.escalation_board = _zero_address()
        self.escalation_board_set = False

    @gl.public.write
    def set_claim_tribunal(self, claim_tribunal_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set claim tribunal")
        if self.claim_tribunal_set:
            raise gl.vm.UserError("claim tribunal already set")
        self.claim_tribunal = _normalize_address(claim_tribunal_address)
        self.claim_tribunal_set = True

    @gl.public.write
    def set_escalation_board(self, escalation_board_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set escalation board")
        if self.escalation_board_set:
            raise gl.vm.UserError("escalation board already set")
        self.escalation_board = _normalize_address(escalation_board_address)
        self.escalation_board_set = True

    def _bump(self, counts: TreeMap[str, u256], key: str, delta: int) -> None:
        counts[key] = u256(int(counts.get(key, u256(0))) + delta)

    @gl.public.write
    def record_outcome(self, vendor, approved: bool, claim_id: u256) -> None:
        if not self.claim_tribunal_set or gl.message.sender_address != self.claim_tribunal:
            raise gl.vm.UserError("only the configured claim tribunal can record an outcome")
        key = str(_normalize_address(vendor))
        ckey = str(int(claim_id))
        status = self.claim_status.get(ckey, "")
        if status == STATUS_PENDING_OVERTURN:
            # The overturn for this exact claim arrived first.
            self.claim_status[ckey] = STATUS_OVERTURNED
            self._bump(self.approved_counts, key, 1)
            return
        if status != "":
            raise gl.vm.UserError("outcome already recorded for this claim")
        self.claim_status[ckey] = STATUS_APPROVED if approved else STATUS_REJECTED
        if approved:
            self._bump(self.approved_counts, key, 1)
        else:
            self._bump(self.rejected_counts, key, 1)

    @gl.public.write
    def record_overturn(self, vendor, claim_id: u256) -> None:
        if not self.escalation_board_set or gl.message.sender_address != self.escalation_board:
            raise gl.vm.UserError("only the configured escalation board can record an overturn")
        key = str(_normalize_address(vendor))
        ckey = str(int(claim_id))
        status = self.claim_status.get(ckey, "")
        if status == STATUS_REJECTED:
            self.claim_status[ckey] = STATUS_OVERTURNED
            self._bump(self.rejected_counts, key, -1)
            self._bump(self.approved_counts, key, 1)
        elif status == "":
            self.claim_status[ckey] = STATUS_PENDING_OVERTURN
        else:
            raise gl.vm.UserError("claim cannot be overturned from its current status")

    @gl.public.view
    def get_claim_status(self, claim_id: u256) -> str:
        return self.claim_status.get(str(int(claim_id)), "")

    @gl.public.view
    def get_vendor_risk(self, vendor) -> str:
        key = str(_normalize_address(vendor))
        approved = int(self.approved_counts.get(key, u256(0)))
        rejected = int(self.rejected_counts.get(key, u256(0)))
        return _risk_category(approved, rejected)

    @gl.public.view
    def get_vendor_stats(self, vendor) -> str:
        key = str(_normalize_address(vendor))
        approved = int(self.approved_counts.get(key, u256(0)))
        rejected = int(self.rejected_counts.get(key, u256(0)))
        stats = {
            "vendor": key,
            "approved": approved,
            "rejected": rejected,
            "risk": _risk_category(approved, rejected),
        }
        return json.dumps(stats)
