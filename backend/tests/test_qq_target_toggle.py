from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes.qq import update_target
from app.models.qq import QQNotificationTarget
from app.schemas.qq import QQTargetUpdate


@pytest.mark.parametrize("enabled", [False, True])
async def test_toggle_preserves_target_and_does_not_recreate_history(enabled):
    created = datetime(2026, 9, 29, tzinfo=UTC)
    target = QQNotificationTarget(
        id=1,
        bot_id=2,
        name="目标",
        group_openid="group",
        is_enabled=not enabled,
        initial_sync_days=3,
        created_at=created,
        message_template="{text}",
        template_variables={},
        all_monitored_users=True,
    )
    db = AsyncMock()
    db.get.return_value = target
    with (
        patch(
            "app.api.routes.qq.validate_target_template", new=AsyncMock(return_value="{text}")
        ) as validate,
        patch("app.api.routes.qq._replace_subscriptions", new=AsyncMock()) as subscriptions,
        patch("app.api.routes.qq.create_target_history_deliveries", new=AsyncMock()) as history,
        patch("app.api.routes.qq._target_out", new=AsyncMock(return_value=target)),
    ):
        result = await update_target(1, QQTargetUpdate(is_enabled=enabled), db, object())
        assert result.is_enabled is enabled
        assert (target.id, target.bot_id, target.group_openid) == (1, 2, "group")
        assert target.initial_sync_days == 3
        assert target.created_at == created
        subscriptions.assert_not_awaited()
        history.assert_not_awaited()
        if enabled:
            validate.assert_awaited_once()
        else:
            validate.assert_not_awaited()
        db.commit.assert_awaited_once()
