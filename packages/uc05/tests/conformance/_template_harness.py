"""COPY-PASTE CONFORMANCE HARNESS TEMPLATE.

You do not write tests to validate a real adapter.  You write this -- about
twenty lines saying how to drive your adapter into each documented state --
append it to the relevant list in ``harness.py``, and the whole conformance
suite runs against your implementation.

    cp tests/conformance/_template_harness.py tests/conformance/company_harness.py

Then, in ``harness.py``:

    from .company_harness import COMPANY_LEARNER_CONTEXT_HARNESS

    LEARNER_CONTEXT_HARNESSES = (
        ...,
        COMPANY_LEARNER_CONTEXT_HARNESS,   # <- one line
    )

and run:

    python -m pytest tests/conformance -q

------------------------------------------------------------------------------
How to drive a real adapter into a failure state
------------------------------------------------------------------------------

Point it at a stub upstream rather than the real one.  The adapter under test
is genuinely your adapter; only what it talks to is substituted.  Typical
approaches, cheapest first:

*   Give the adapter a base URL pointing at a local stub server that returns
    the payload or status you want.
*   Construct the adapter with an injected HTTP client whose transport is
    scripted (``httpx.MockTransport`` and equivalents).
*   For ``malformed`` and ``invalid_value``, feed a captured real payload that
    you have edited -- this is the highest-value case, because it is the one
    that catches a mapping that silently guesses.

``leak_markers`` must list your upstream's field names, its error strings and
your vendor's name.  The suite fails if any of them appears in a returned value
or a raised error.  Be generous: this is the check that keeps upstream shapes
from spreading past the adapter.
"""

from __future__ import annotations

from .harness import PortHarness

# TODO(import): your adapter.
# from uc05.adapters.real.company_learner_context import CompanyLearnerContextAdapter

# TODO(stub): however you script the upstream for tests.
# def _adapter(payload_or_status):
#     return CompanyLearnerContextAdapter(
#         settings=test_settings(),
#         client=httpx.AsyncClient(transport=httpx.MockTransport(...)),
#     )


COMPANY_LEARNER_CONTEXT_HARNESS = PortHarness(
    name="company",  # TODO: your provider key
    port="learner_context_provider",
    # TODO(leak markers): upstream field names, error strings, vendor name.
    leak_markers=("companyApiField", "X-Company-Trace", "CompanyCorp"),
    # TODO(happy): a normal, successful response.
    happy=lambda: _not_implemented("happy"),
    # TODO(unavailable): upstream refuses or is unreachable -> ProviderUnavailable
    unavailable=lambda: _not_implemented("unavailable"),
    # TODO(timeout): upstream exceeds the budget -> ProviderTimeout
    timeout=lambda: _not_implemented("timeout"),
    # TODO(malformed): unparseable payload -> ProviderInvalidResponse
    malformed=lambda: _not_implemented("malformed"),
    # TODO(invalid_value): a level that maps to no platform enum member.
    #   Must yield LEVEL_5 / source "default" / status "invalid" -- never a guess.
    invalid_value=lambda: _not_implemented("invalid_value"),
    # TODO(empty): upstream answered and had nothing.  Distinct from unavailable.
    empty=lambda: _not_implemented("empty"),
    # TODO(slow): answers eventually; proves the caller's budget binds.
    #   Set to None if you genuinely cannot script it -- that state then skips.
    slow=None,
    # TODO(expectations): what the happy case must yield, in platform terms.
    expectations={"level": "LEVEL_6", "practice_area": "Employment"},
)


def _not_implemented(state: str):  # pragma: no cover - template only
    raise NotImplementedError(
        f"TODO: build the adapter in its {state!r} state. See the module "
        f"docstring for how to script an upstream without a network."
    )
