from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.monitored_user import MonitoredUserCreate, MonitoredUserUpdate
from app.schemas.polling import PollingSettingsPatch
from app.schemas.system import HealthResponse


def test_x_username_is_normalized() -> None:
    payload = MonitoredUserCreate(username="  @OpenAI  ")
    assert payload.username == "openai"


@pytest.mark.parametrize(
    "username", ["", "bad-name", "space name", "a" * 16, "用户", "ｏｐｅｎａｉ"]
)
def test_invalid_x_username_is_rejected(username: str) -> None:
    with pytest.raises(ValidationError):
        MonitoredUserCreate(username=username)


@pytest.mark.parametrize("field", ["include_replies", "include_retweets"])
def test_filter_update_rejects_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError):
        MonitoredUserUpdate(**{field: None})
    assert MonitoredUserUpdate().model_dump(exclude_unset=True) == {}


def test_settings_patch_is_partial_but_rejects_null() -> None:
    patch = PollingSettingsPatch(max_concurrency=8)
    assert patch.model_dump(exclude_unset=True) == {"max_concurrency": 8}
    with pytest.raises(ValidationError):
        PollingSettingsPatch(global_poll_interval_seconds=None)


def test_datetimes_are_serialized_as_utc() -> None:
    response = HealthResponse(
        status="ok",
        version="test",
        timestamp=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    assert '"timestamp":"2026-01-02T03:04:05Z"' in response.model_dump_json()
