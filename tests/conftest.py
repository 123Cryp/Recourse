"""
Workaround for a genlayer-test 0.29.2 Direct Mode restriction (not yet
confirmed whether intentional or version-specific): the SDK's Direct Mode
loader tracks a module-level singleton (`__known_contract__` in
`genlayer.gl.genvm_contracts`) meant to catch two `gl.Contract` subclasses
defined in the SAME file, but it is never reset between separate
`direct_deploy(...)` calls for DIFFERENT contract files within one test
process -- so the second deploy of a different contract type in the same
test raises `TypeError: only one contract is allowed`. This is exactly
what a multi-contract system like Recourse's _wire_up() needs (deploying
VendorLedger, ClaimTribunal, and EscalationBoard together).

This fixture override resets that singleton immediately before every
direct_deploy call, once it exists in sys.modules (it isn't importable
before the first deploy sets up the SDK's sys.path).
"""

import sys

import pytest


@pytest.fixture
def direct_deploy(direct_deploy):
    def _deploy(*args, **kwargs):
        module = sys.modules.get("genlayer.gl.genvm_contracts")
        if module is not None:
            module.__known_contract__ = None
        return direct_deploy(*args, **kwargs)

    return _deploy
