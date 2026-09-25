"""
Shared test bootstrap - wires up the offline genlayer SDK stub and
loads all three Recourse contracts against it, once. Standard pattern
used across this project's test files (adapted from prior single-
contract projects to handle Recourse's three cross-calling contracts).
"""
import importlib.util
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

_CONTRACTS_DIR = os.path.join(os.path.dirname(_THIS_DIR), "contracts")


def _load(module_name, filename):
    path = os.path.join(_CONTRACTS_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_vendor_ledger_mod = _load("recourse_vendor_ledger", "vendor_ledger.py")
_claim_tribunal_mod = _load("recourse_claim_tribunal", "claim_tribunal.py")
_escalation_board_mod = _load("recourse_escalation_board", "escalation_board.py")

VendorLedger = _vendor_ledger_mod.VendorLedger
ClaimTribunal = _claim_tribunal_mod.ClaimTribunal
EscalationBoard = _escalation_board_mod.EscalationBoard

# All three modules resolve `from genlayer import *` to the exact same
# already-imported stub package, so this `gl` (and Address, u256) is
# shared and consistent across all three contracts.
gl = _vendor_ledger_mod.gl
Address = _vendor_ledger_mod.Address
u256 = _vendor_ledger_mod.u256

from genlayer import register_contract  # noqa: E402  (stub-only helper)

# Fixed, valid, distinct addresses reused across test files.
DEPLOYER_ADDRESS = "0x" + "aa" * 20
CLAIMANT_ADDRESS = "0x" + "11" * 20
STRANGER_ADDRESS = "0x" + "33" * 20
CLEAN_VENDOR_ADDRESS = "0x" + "44" * 20
BAD_VENDOR_ADDRESS = "0x" + "55" * 20

VENDOR_LEDGER_ADDR = "0x" + "01" * 20
CLAIM_TRIBUNAL_ADDR = "0x" + "02" * 20
ESCALATION_BOARD_ADDR = "0x" + "03" * 20


def set_caller(address_str):
    """Simulate a specific wallet/contract calling the next method."""
    gl.message.sender_address = Address(address_str)


def deploy(cls, address_str, *args, sender=DEPLOYER_ADDRESS):
    """Deploy (construct + register) a contract at a fixed test
    address, as `sender`."""
    set_caller(sender)
    instance = cls(*args)
    register_contract(address_str, instance)
    return instance


def wire_up():
    """Deploy and fully wire VendorLedger + ClaimTribunal +
    EscalationBoard, mirroring the 7-step sequence documented in
    DESIGN_DECISIONS.md section 5."""
    ledger = deploy(VendorLedger, VENDOR_LEDGER_ADDR)
    tribunal = deploy(ClaimTribunal, CLAIM_TRIBUNAL_ADDR, Address(VENDOR_LEDGER_ADDR))
    board = deploy(EscalationBoard, ESCALATION_BOARD_ADDR, Address(VENDOR_LEDGER_ADDR))

    set_caller(DEPLOYER_ADDRESS)
    tribunal.set_escalation_board(Address(ESCALATION_BOARD_ADDR))
    board.set_claim_tribunal(Address(CLAIM_TRIBUNAL_ADDR))
    ledger.set_claim_tribunal(Address(CLAIM_TRIBUNAL_ADDR))
    ledger.set_escalation_board(Address(ESCALATION_BOARD_ADDR))

    return ledger, tribunal, board
