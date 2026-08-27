"""EXAMPLE conformance file - the one command that grades the new adapter.

Copy to tests/, point the fixture at your adapter, and run:

    pytest tests/test_company_authority_adapter.py -v
"""

import pytest

from uc03.conformance import LegalAuthorityProviderConformance

from .company_authority_adapter import CompanyAuthorityAdapter


class TestCompanyAuthorityAdapter(LegalAuthorityProviderConformance):
    @pytest.fixture
    def adapter(self):
        return CompanyAuthorityAdapter(
            base_url="https://authority.internal", api_key="…"
        )
