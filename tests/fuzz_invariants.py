"""
Randomized invariant checks for the parts of Recourse whose correctness is
structural: the evidence-URL parser, the verdict interpreter, and the
ledger's per-claim accounting. It proves properties such as "an approval
always carries a quote that is present in the evidence"; it cannot prove
that a quote actually supports a claim (that is a semantic judgment).

Run with: python3 tests/fuzz_invariants.py
"""
import sys, json, random, string
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap as B
T = B._claim_tribunal_mod
random.seed(1234)

# ---------- 1) URL parser: never crashes; anything accepted is a plain https URL
alphabet = string.ascii_letters + string.digits + "./:@?#%\\[] -_~!$&'()*+,;=\t\n" + "é"
pieces = ["https://", "http://", "HTTPS://", "example.com", "example.test", "/", "..", ".", "%2e", "%2F",
          "%5c", "@", ":443", ":8443", "?q=1", "#f", "\\", "[::1]", "a", "policies", "..%2f", " ", "\t"]
bad_host_chars = set(":@[]%?#\\ \t\n")
accepted = 0
for i in range(60000):
    if i % 2:
        u = "".join(random.choice(pieces) for _ in range(random.randint(1, 8)))
    else:
        u = "https://" + "".join(random.choice(alphabet) for _ in range(random.randint(0, 40)))
    host, path = T._parse_https_url(u)          # must never raise
    if host:
        accepted += 1
        s = u.strip()
        assert s.lower().startswith("https://"), u
        assert len(s) <= T.MAX_URL_CHARS, u
        assert not (set(host) & bad_host_chars), (u, host)
        assert "." in host and not host.startswith(".") and not host.endswith("."), (u, host)
        assert path.startswith("/"), (u, path)
        assert not any(c in path for c in "?#\\ \t\n"), (u, path)
        assert ".." not in path.split("/") and "." not in path.split("/"), (u, path)
        assert "%2e" not in path.lower() and "%2f" not in path.lower() and "%5c" not in path.lower(), (u, path)
        assert s[8:].lower().startswith(host), (u, host)
print("1) URL parser: 60000 inputs, no crash, all", accepted, "accepted URLs satisfy every rule")

# ---------- 2) verdict interpreter: never crashes; APPROVED only when fully grounded
evidence = "Example Domain. This domain is for use in documentation examples without needing permission."
quotes = ["", "Example", "This domain is for use in documentation examples", "the warranty covers everything",
          "this   DOMAIN is for use in documentation examples", None, 5, ["x"]]
decisions = ["APPROVED", "REJECTED", "MAYBE", "", None, 1, "approved"]
readings_sets = [["literal_reading"], ["literal_reading", "reasonable_reading"],
                 ["literal_reading", "reasonable_reading", "skeptical_reading"]]
counts = {"APPROVED": 0, "REJECTED": 0, "INVALID": 0}
for i in range(60000):
    readings = random.choice(readings_sets)
    obj = {"final_decision": random.choice(decisions), "quoted_evidence": random.choice(quotes)}
    for r in readings + ["skeptical_reading", "intent_reading"]:
        if random.random() < 0.85:
            obj[r] = random.choice(["APPROVED", "APPROVED", "REJECTED", None])
    kind = random.random()
    if kind < 0.1:
        raw = "garbage " + str(i)
    elif kind < 0.2:
        raw = "```json\n" + json.dumps(obj) + "\n```"
    elif kind < 0.25:
        raw = json.dumps([obj])
    else:
        raw = json.dumps(obj)
    out = T._interpret(raw, evidence, readings)      # must never raise
    d = out["final_decision"]
    counts[d] += 1
    assert d in ("APPROVED", "REJECTED", "INVALID")
    if d == "APPROVED":
        q = T._norm(out["quoted_evidence"])
        assert len(q) >= 15 and q in T._norm(evidence), (raw, out)
        assert all(obj.get(r) == "APPROVED" for r in readings), (raw, out)
        assert obj.get("final_decision") == "APPROVED", (raw, out)
    else:
        assert out["quoted_evidence"] == ""
print("2) interpreter: 60000 outputs, no crash, outcomes", counts, "- every APPROVED carries an in-evidence quote and all required readings")

# ---------- 3) ledger: random call sequences vs a simple reference model
from genlayer import UserError
def model_counts(status):
    a = sum(1 for s in status.values() if s in ("A", "O"))
    r = sum(1 for s in status.values() if s == "R")
    return a, r
V = B.Address(B.CLEAN_VENDOR_ADDRESS)
for trial in range(400):
    ledger, tribunal, board = B.wire_up()
    ref = {}                 # claim -> A / R / O / P
    ids = [B.next_claim_id() for _ in range(6)]
    for step in range(40):
        cid = random.choice(ids)
        if random.random() < 0.6:
            approved = random.random() < 0.5
            B.set_caller(tribunal.address)
            st = ref.get(cid, "")
            try:
                ledger.record_outcome(V, approved, cid)
                assert st in ("", "P"), ("outcome accepted twice", st)
                ref[cid] = "O" if st == "P" else ("A" if approved else "R")
            except UserError:
                assert st in ("A", "R", "O"), ("outcome refused wrongly", st)
        else:
            B.set_caller(board.address)
            st = ref.get(cid, "")
            try:
                ledger.record_overturn(V, cid)
                assert st in ("R", ""), ("overturn accepted wrongly", st)
                ref[cid] = "O" if st == "R" else "P"
            except UserError:
                assert st in ("A", "O", "P"), ("overturn refused wrongly", st)
        s = json.loads(ledger.get_vendor_stats(V))
        assert (s["approved"], s["rejected"]) == model_counts(ref), (trial, step, s, ref)
        assert s["approved"] >= 0 and s["rejected"] >= 0
print("3) ledger: 400 random sequences x 40 calls, counts always match one-claim-one-count model")
