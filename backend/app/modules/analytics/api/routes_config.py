"""Configuration inspection and validation routes (spec sections 16, 19).

The validation endpoint exists so an operator can find out what a threshold
change would do *before* restarting the service with it. It never applies
anything: settings are immutable for the lifetime of an application instance.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query

from app.modules.analytics.api.deps import AdminDep, get_settings
from app.modules.analytics.config import (
    AnalyticsSettings,
    ConfigurationReport,
    validate_settings_payload,
)

router = APIRouter(
    prefix="/analytics/config", tags=["Analytics configuration (UC-10)"]
)

SettingsDep = Annotated[AnalyticsSettings, Depends(get_settings)]


@router.get(
    "",
    summary="Effective configuration",
    description=(
        "Configuration in force for this instance, with API keys omitted - only "
        "the number of configured keys is reported. Includes any warnings the "
        "current values raise."
    ),
)
async def get_effective_configuration(
    settings: SettingsDep,
    _admin: AdminDep,
) -> dict[str, Any]:
    return {
        "effective": settings.public_dump(),
        "issues": [issue.model_dump(mode="json") for issue in settings.issues()],
    }


@router.post(
    "/validate",
    response_model=ConfigurationReport,
    summary="Validate a candidate configuration",
    description=(
        "Checks candidate values without applying them. Out-of-range values are "
        "reported as ERROR, unusual ones as WARNING, and values dangerous enough "
        "to break the review workflow as DANGEROUS. A payload containing dangerous "
        "values is reported invalid with requires_confirmation=true until the "
        "caller passes confirm_dangerous=true, so such a setting can never be "
        "adopted by accident. Values the payload omits are taken from the "
        "configuration this instance is running with."
    ),
)
async def validate_configuration(
    settings: SettingsDep,
    _admin: AdminDep,
    payload: Annotated[
        dict[str, Any],
        Body(
            description="Candidate settings, e.g. {\"flag_wrong_answer_rate_threshold\": 55}",
            examples=[{"flag_wrong_answer_rate_threshold": 55, "flag_min_responses": 10}],
        ),
    ],
    confirm_dangerous: Annotated[
        bool,
        Query(description="Explicitly accept DANGEROUS values found in the payload."),
    ] = False,
) -> ConfigurationReport:
    return validate_settings_payload(
        payload, confirm_dangerous=confirm_dangerous, base=settings
    )
