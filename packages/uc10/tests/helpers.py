"""Helpers shared by the integration tests."""

from __future__ import annotations

from datetime import timedelta

from tests.canaries import canary_comment
from uc10.adapters.mock.interaction_provider import InteractionSpec, MockInteractionProvider
from uc10.application.flagging_service import FlaggingService
from uc10.domain.enums import RatingValue


def seed_via_api(
    client,
    provider: MockInteractionProvider,
    *,
    total: int,
    downs: int,
    topic_tag: str,
    with_comments: bool = False,
) -> list[str]:
    """Register ``total`` interactions and rate each one through the HTTP API.

    Every rating comes from a different learner, so the resulting rate is a rolling
    cross-user rate rather than one learner's opinion counted repeatedly.
    """
    interaction_ids = []
    for index in range(total):
        interaction_id = f"int_{topic_tag}_{index}"
        user_id = f"user_seed_{index}"
        provider.register(
            InteractionSpec(
                interaction_id=interaction_id,
                topic_tag=topic_tag,
                user_id=user_id,
                delivered_offset=timedelta(minutes=5),
            )
        )
        down = index < downs
        body = {"rating": RatingValue.DOWN.value if down else RatingValue.UP.value}
        if down and with_comments:
            body["comment"] = canary_comment(str(index))
        response = client.post(
            f"/api/v1/interactions/{interaction_id}/rating",
            json=body,
            headers={"X-User-Id": user_id},
        )
        assert response.status_code == 201, response.text
        interaction_ids.append(interaction_id)
    return interaction_ids


def seed_repository(repository, records) -> None:
    for record in records:
        repository.save(record)


def flagging_service(
    *, ratings, flags, work_queue, notifications, config, clock
) -> FlaggingService:
    return FlaggingService(
        ratings=ratings,
        flags=flags,
        work_queue=work_queue,
        notifications=notifications,
        config=config,
        clock=clock,
    )
