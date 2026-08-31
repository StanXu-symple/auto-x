import pytest
from pydantic import ValidationError

from app.api.errors import APIError
from app.api.routes.ai import _ensure_provider_host_allowed
from app.models.ai import AISetting
from app.schemas.ai import AIDraftPatch, AISettingsPatch, AISkillPatch, ManualGenerateRequest


def test_ai_settings_patch_is_partial_and_supports_multi_skill() -> None:
    patch = AISettingsPatch(
        model="gpt-5.6-terra",
        reasoning_effort="max",
        default_skill_ids=[3, 1],
    )
    assert patch.model_dump(exclude_unset=True) == {
        "model": "gpt-5.6-terra",
        "reasoning_effort": "max",
        "default_skill_ids": [3, 1],
    }


@pytest.mark.parametrize("field", ["enabled", "provider", "base_url", "default_skill_ids"])
def test_ai_settings_reject_explicit_null(field: str) -> None:
    with pytest.raises(ValidationError):
        AISettingsPatch(**{field: None})


def test_ai_settings_reject_url_credentials_and_duplicate_skills() -> None:
    with pytest.raises(ValidationError):
        AISettingsPatch(base_url="https://token@example.com/v1")
    with pytest.raises(ValidationError):
        AISettingsPatch(default_skill_ids=[1, 1])


def test_skill_patch_requires_a_change_and_tracks_nullable_fields() -> None:
    with pytest.raises(ValidationError):
        AISkillPatch()
    patch = AISkillPatch(description=None, output_schema=None)
    assert patch.model_dump(exclude_unset=True) == {
        "description": None,
        "output_schema": None,
    }


def test_manual_generation_idempotency_key_is_strict_ascii() -> None:
    assert ManualGenerateRequest(idempotency_key="request-1234").idempotency_key
    with pytest.raises(ValidationError):
        ManualGenerateRequest(idempotency_key="包含中文")


def test_draft_update_requires_revision_and_content_change() -> None:
    with pytest.raises(ValidationError):
        AIDraftPatch(revision=1)
    patch = AIDraftPatch(revision=2, status="approved")
    assert patch.status == "approved"


def test_settings_api_rejects_non_allowlisted_provider_host() -> None:
    setting = AISetting(
        id=1,
        enabled=True,
        auto_generate=True,
        provider="openai_responses",
        model_name="gpt-5.6-terra",
        base_url="https://attacker.invalid/v1",
        language="zh-CN",
        tone="专业自然",
        require_review=True,
        reasoning_effort="medium",
        default_skill_ids=[],
        max_attempts=3,
        max_output_tokens=1000,
        request_timeout_seconds=30,
    )
    with pytest.raises(APIError) as error:
        _ensure_provider_host_allowed(setting)
    assert error.value.status_code == 422
    assert error.value.code == "provider_host_not_allowed"
