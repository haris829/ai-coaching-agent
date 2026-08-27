"""WHERE THE REAL INTEGRATIONS GO.

This package is intentionally empty of behaviour. When a real API becomes available:

1. Add a module here (``naric.py``, ``courses.py``, ``cases.py``, ``profile.py``).
2. Implement the matching Protocol from ``uc01/contracts/services.py``.
3. Map the real payload into the UC-01 domain types inside that module. Nowhere else.
4. Catch every transport/serialisation error and re-raise one of the three contract
   exceptions from ``uc01/contracts/exceptions.py``, attaching the technical detail for
   server-side logging only.
5. Register the class in ``uc01/api/container.py`` (a single ``if`` per dependency,
   driven by the ``UC01_*_ADAPTER`` environment variables).

Do not add UC-01 business rules here, and do not let a real payload shape leak past this
package. See ``docs/ADAPTER_REPLACEMENT.md`` for a worked example and
``uc01/adapters/real/template.py`` for a copy-paste skeleton.
"""
