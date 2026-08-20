"""UC-09 — Formal Assessment Mode.

A formal attempt is an ordinary UC-03 attempt sat under supervision conditions: identity confirmed,
one device, no pause, no resume after a disconnect, no AI coaching while it runs, and — the rule the
rest of the module exists to protect — **no certificate until a human assessor has approved the
pass**.

UC-09 owns no quiz logic, no scoring engine, no certificate generator and no schema. It owns the
conditions, the identity confirmation, the device lock, the formal lifecycle, the human review and
the certificate gate, and it reaches everything else through the ports in ``integration``.
"""
