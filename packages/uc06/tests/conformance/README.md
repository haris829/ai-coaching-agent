# Port conformance kit

A reusable, adapter-agnostic suite. Every implementation of a UC-06 port must
pass it, including yours.

It asserts the **behavioural contract**, not the mock's data: return types,
typed failure modes, normalisation to the platform contract, boundary hygiene
(no upstream field names, error text or provider names escaping), and honoured
timeouts. It is parameterised on the adapter under test — nothing here is
hard-coded to the mock.

## Pointing it at your adapter

Declare a module-level `CONFORMANCE_SCENARIOS` dict in your adapter file, naming
the identifier in your system for each contract case. The kit reads it from your
module, so **no test file is edited**. Then register your adapter in
`PROVIDER_REGISTRY` (`uc06/composition.py`) and run:

```
python -m pytest tests/conformance -q --adapter-family=<your-registry-name>
```

`--adapter-family` selects, for every port, the registered provider of that name
if one exists, and leaves the others at their defaults. To run one port only:

```
python -m pytest tests/conformance/test_case_file_provider_conformance.py -q --adapter-family=<name>
```

With no flag the suite runs against every registered provider for each port, so
CI covers the mock and foreign families automatically, and covers your adapter
from the moment it is registered.

**No new test needs writing to validate a real adapter.**

## What a failure means

| Failure | What it tells you |
|---|---|
| `returns the platform type` | Your mapping returns an upstream object. Map it in the adapter. |
| `raises a contract exception` | An upstream exception escaped. Translate it. |
| `no upstream detail escapes` | A field name, error string or provider name is leaking past the boundary. |
| `normalises to the platform enum` | An upstream value reached the domain unmapped, or an unmappable value was silently defaulted instead of raising `ProviderInvalidResponse`. |
| `read-only surface` | A mutating method was added to a read-only port. |
| `declares no CONFORMANCE_SCENARIOS` | Your adapter module needs the scenario map. The message lists the required keys. |
| `names no identifier for the '<case>' contract case` | A contract case is unexercised. If your upstream genuinely cannot produce it, raise it as a contract question rather than leaving it untested. |
