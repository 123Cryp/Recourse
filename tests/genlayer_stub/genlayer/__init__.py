"""
Minimal offline stub of the `genlayer` SDK, extended from the pattern
used on prior projects (e.g. TrueStake) to additionally support
cross-contract calls via `gl.get_contract_at(address)`, which a
single-contract project never needed.

THIS IS A TEST-ONLY SHIM, NOT PART OF THE DEPLOYABLE CONTRACT.

It intentionally does NOT attempt to simulate:
  - real network access, real LLM behavior, or multi-validator consensus
    (gl.nondet.web.render / gl.nondet.exec_prompt must be monkeypatched
    per test case, and eq_principle.* simply calls the given function
    once and returns its result -- no independent leader/validator
    re-execution, no NLP comparator),
  - GenVM's real asynchronous cross-contract `.emit()` (this stub runs
    the target method synchronously, in-process, for test simplicity --
    the real async-write behavior was confirmed separately via live
    testing on GenLayer Studio; see LESSONS_LEARNED.md),
  - GenVM's real deterministic clock.

What it DOES reproduce faithfully, because Recourse's design depends on
it and it was live-confirmed on Studio:
  - `gl.get_contract_at(addr).view()` / `.emit()` resolve to whichever
    contract instance was registered at that address via
    `register_contract()`.
  - Inside a method reached through `.emit()`, `gl.message.sender_address`
    is the *calling contract's own address*, not the original human
    caller -- tracked here via a small call stack pushed/popped by the
    `@gl.public.write` / `@gl.public.view` decorators themselves.
"""

__all__ = ["gl", "TreeMap", "u256", "DynArray", "i256", "bigint", "Address"]


class _SubscriptableContainer:
    """Base for storage-type stand-ins that support `Type[K, V]` syntax
    used in class-level annotations (e.g. `TreeMap[str, str]`)."""

    def __class_getitem__(cls, item):
        return cls


class TreeMap(_SubscriptableContainer, dict):
    """Stand-in for genlayer's persistent TreeMap - behaves like a dict."""


class DynArray(_SubscriptableContainer, list):
    """Stand-in for genlayer's persistent DynArray - behaves like a list."""


class u256(int):
    """Stand-in for genlayer's fixed-width unsigned integer type."""


class i256(int):
    """Stand-in for genlayer's fixed-width signed integer type."""


class bigint(int):
    """Stand-in for genlayer's arbitrary-precision integer type."""


class Address:
    """
    Minimal stand-in for genlayer's Address type: validates a
    40-hex-character, "0x"-prefixed string (or raw 20-byte value, as
    contracts' `_normalize_address` helpers produce via
    `int.to_bytes(20, "big")`) and compares/hashes case-insensitively,
    matching how EVM-style addresses behave.
    """

    def __init__(self, value):
        if isinstance(value, (bytes, bytearray)):
            text = "0x" + value.hex()
        else:
            text = str(value)
        if not text.startswith("0x") or len(text) != 42:
            raise ValueError(f"invalid address: {text!r}")
        int(text[2:], 16)  # raises ValueError if not valid hex
        self._value = text

    def __str__(self):
        return self._value

    def __repr__(self):
        return f"Address({self._value!r})"

    def __eq__(self, other):
        return str(self).lower() == str(other).lower()

    def __hash__(self):
        return hash(str(self).lower())


class UserError(Exception):
    """Stand-in for genlayer.gl.vm.UserError."""


class _Vm:
    """Stand-in for `gl.vm` - exposes UserError at its real SDK path."""

    UserError = UserError


# Call stack of contract instances currently executing a
# @gl.public.write/.view method, innermost last. Used so `.emit()`
# called from inside a method knows which contract is "calling out".
_CALL_STACK = []


def _wrap_public(fn):
    def wrapper(self, *args, **kwargs):
        _CALL_STACK.append(self)
        try:
            return fn(self, *args, **kwargs)
        finally:
            _CALL_STACK.pop()

    return wrapper


class _WriteDecorator:
    """Stand-in for `gl.public.write` / `gl.public.write.payable`."""

    def __call__(self, fn):
        return _wrap_public(fn)

    @staticmethod
    def payable(fn):
        return _wrap_public(fn)


class _PublicNamespace:
    """Stand-in for `gl.public`."""

    write = _WriteDecorator()

    @staticmethod
    def view(fn):
        return _wrap_public(fn)


class _NondetWeb:
    """Stand-in for `gl.nondet.web`. `render` raises by default; tests
    monkeypatch this to simulate specific fetch outcomes."""

    @staticmethod
    def render(url, mode="text"):
        raise NotImplementedError("gl.nondet.web.render must be patched in tests")


class _Nondet:
    web = _NondetWeb()

    @staticmethod
    def exec_prompt(prompt, response_format="text"):
        raise NotImplementedError("gl.nondet.exec_prompt must be patched in tests")


class _EqPrinciple:
    """
    Stand-in for `gl.eq_principle`. For offline unit tests this simply
    runs `fn` once and returns its result -- simulating independent
    leader/validator re-execution and the real NLP comparator requires
    the live GenLayer Studio/testnet (already done for Recourse; see
    LESSONS_LEARNED.md).
    """

    @staticmethod
    def strict_eq(fn):
        return fn()

    @staticmethod
    def prompt_comparative(fn, principle=None):
        return fn()

    @staticmethod
    def prompt_non_comparative(fn, task="", criteria=""):
        return fn()


class _Message:
    """
    Stand-in for `gl.message`. `sender_address` is a plain mutable
    attribute here - tests (or `.emit()`, automatically) set it before
    each call to simulate a specific caller.
    """

    sender_address = None
    value = u256(0)


class _Contract:
    """
    Stand-in base class for `gl.Contract`. Persistent-storage-typed
    fields (TreeMap, DynArray) are pre-populated empty before the
    contract's own `__init__` runs, matching GenVM's real zero-init
    behavior for those types.
    """

    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        for klass in reversed(cls.__mro__):
            for name, annotation in vars(klass).get("__annotations__", {}).items():
                if isinstance(annotation, type) and issubclass(annotation, (TreeMap, DynArray)):
                    setattr(instance, name, annotation())
        instance.address = None
        return instance

    def __init__(self, *args, **kwargs):
        pass


# Registry of deployed contract instances, keyed by lowercased address
# string, so gl.get_contract_at(addr) can resolve them.
_CONTRACT_REGISTRY = {}


def register_contract(address, instance):
    """Test-harness-only helper (not part of the real SDK surface):
    record a deployed contract instance under `address` so
    gl.get_contract_at(address) can find it, and stamp the instance
    with its own address (needed so .emit() calls made *from* this
    contract can report the right caller)."""
    addr = address if isinstance(address, Address) else Address(address)
    instance.address = addr
    _CONTRACT_REGISTRY[str(addr).lower()] = instance
    return instance


class _ContractCallProxy:
    """Returned by `.view()`/`.emit()`; forwards one attribute access
    (a method name) into an actual call on the target instance, with
    sender_address temporarily overridden for `.emit()`."""

    def __init__(self, target, caller_address=None):
        self._target = target
        self._caller_address = caller_address

    def __getattr__(self, method_name):
        def _call(*args, **kwargs):
            # Must set the attribute on the `gl.message` INSTANCE, not
            # the `_Message` class -- `set_caller()` already shadows the
            # class attribute with an instance attribute on `_GL.message`,
            # so writing to the class here would silently have no effect
            # on what `gl.message.sender_address` actually reads.
            previous_sender = _GL.message.sender_address
            if self._caller_address is not None:
                _GL.message.sender_address = self._caller_address
            try:
                return getattr(self._target, method_name)(*args, **kwargs)
            finally:
                _GL.message.sender_address = previous_sender

        return _call


class _ContractHandle:
    """Returned by `gl.get_contract_at(address)`."""

    def __init__(self, address):
        self._address = address if isinstance(address, Address) else Address(address)

    def _resolve(self):
        key = str(self._address).lower()
        if key not in _CONTRACT_REGISTRY:
            raise UserError(f"no contract registered at {key}")
        return _CONTRACT_REGISTRY[key]

    def view(self):
        return _ContractCallProxy(self._resolve())

    def emit(self, value=None):
        caller_instance = _CALL_STACK[-1] if _CALL_STACK else None
        caller_address = getattr(caller_instance, "address", None)
        return _ContractCallProxy(self._resolve(), caller_address=caller_address)


def get_contract_at(address):
    return _ContractHandle(address)


class _GL:
    Contract = _Contract
    public = _PublicNamespace()
    nondet = _Nondet()
    eq_principle = _EqPrinciple()
    vm = _Vm()
    message = _Message()
    get_contract_at = staticmethod(get_contract_at)


gl = _GL()
