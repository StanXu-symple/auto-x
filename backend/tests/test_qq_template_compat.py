from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.schemas.qq import QQTargetCreate, QQTargetUpdate, normalize_message_template
from app.services.qq_notifications import render_qq_message


@pytest.mark.parametrize("template", ["{{username}}：{{text}}", "{username}：{text}"])
def test_saved_and_new_templates_render_variables(template):
    tweet = SimpleNamespace(text="正文包含 {username}", tweet_id="123", posted_at=datetime.now(UTC))
    user = SimpleNamespace(username="alice", display_name="昵称")
    assert render_qq_message(template, tweet=tweet, user=user) == "alice：正文包含 {username}"


def test_save_normalizes_legacy_placeholders():
    payload = QQTargetCreate(
        bot_id=1,
        name="test",
        group_openid="abc",
        all_monitored_users=True,
        message_template="{{author}} {{text}}",
    )
    assert payload.message_template == "{author} {text}"
    assert QQTargetUpdate(message_template="{{username}}").message_template == "{username}"


def test_custom_variables_and_literal_braces():
    assert normalize_message_template("{{topic}}", {"topic": "资讯"}) == "{topic}"
    assert normalize_message_template("{{literal}}") == "{{literal}}"
    assert normalize_message_template("{{{{username}}}}") == "{{{{username}}}}"
