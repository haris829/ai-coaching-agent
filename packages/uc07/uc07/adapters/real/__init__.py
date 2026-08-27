"""Real (company) adapters.

Empty by design: no real upstream exists yet. Copy ``_template.py``, implement it
against the company API, add one registry line in ``uc07/composition.py`` and set
one environment variable. See docs/INTEGRATION.md.

``_template.py`` is a template, not a provider: it is never registered and never
instantiated by the composition root.
"""
