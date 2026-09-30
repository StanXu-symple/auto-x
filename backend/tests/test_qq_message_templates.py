import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_current_admin
from app.api.errors import APIError
from app.api.routes.qq_message_templates import router
from app.db.base import Base
from app.db.session import get_db
from app.main import api_error_handler
from app.models.qq import QQNotificationTarget
from app.models.qq_message_template import QQMessageTemplate
from app.models.qq_placeholder import QQPlaceholder
from app.schemas.qq_placeholder import DEFAULT_PLACEHOLDERS


@pytest.fixture
async def context():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda connection: Base.metadata.create_all(
                connection,
                tables=[
                    QQMessageTemplate.__table__,
                    QQPlaceholder.__table__,
                    QQNotificationTarget.__table__,
                ],
            )
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add_all([QQPlaceholder(**row) for row in DEFAULT_PLACEHOLDERS])
        await db.commit()
        app = FastAPI()
        app.include_router(router)
        app.add_exception_handler(APIError, api_error_handler)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_admin] = lambda: object()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, db
    await engine.dispose()


async def test_crud_pagination_search_and_target_copy_isolation(context):
    client, db = context
    ids = []
    for i in range(17):
        response = await client.post(
            "/qq/message-templates",
            json={
                "name": f"模板 {i}",
                "message_template": "{{author}}：{text} {topic}",
                "template_variables": {"topic": "科技"},
            },
        )
        assert response.status_code == 201, response.text
        item = response.json()
        assert item["message_template"] == "{author}：{text} {topic}"
        ids.append(item["id"])
    result = (await client.get("/qq/message-templates?page=2&page_size=15")).json()
    assert result["total"] == 17
    assert len(result["items"]) == 2
    assert (await client.get("/qq/message-templates?search=模板%2016")).json()["total"] == 1
    target = QQNotificationTarget(
        bot_id=1,
        name="目标",
        group_openid="abc",
        message_template=item["message_template"],
        template_variables=item["template_variables"],
    )
    db.add(target)
    await db.commit()
    response = await client.put(
        f"/qq/message-templates/{ids[-1]}",
        json={"name": "修改后", "message_template": "{text}", "template_variables": {}},
    )
    assert response.status_code == 200
    assert (await client.get(f"/qq/message-templates/{ids[-1]}")).json()["name"] == "修改后"
    assert (await client.delete(f"/qq/message-templates/{ids[-1]}")).status_code == 200
    await db.refresh(target)
    assert target.message_template == "{author}：{text} {topic}"
    assert target.template_variables == {"topic": "科技"}
    assert (await client.get(f"/qq/message-templates/{ids[-1]}")).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"name": " ", "message_template": "{text}"},
        {"name": "x", "message_template": " "},
        {"name": "x", "message_template": "{missing}"},
        {"name": "x", "message_template": "{text.attr}"},
        {"name": "x", "message_template": "{text}", "template_variables": {"text": "覆盖"}},
    ],
)
async def test_rejects_empty_invalid_and_unconfigured_templates(context, payload):
    client, _ = context
    assert (await client.post("/qq/message-templates", json=payload)).status_code == 422


async def test_duplicate_name_does_not_overwrite_existing_template(context):
    client, _ = context
    payload = {"name": "模板", "message_template": "{text}"}
    first = (await client.post("/qq/message-templates", json=payload)).json()
    duplicate = await client.post(
        "/qq/message-templates", json={**payload, "name": " 模板 ", "message_template": "{author}"}
    )
    assert duplicate.status_code == 409
    assert (await client.get(f"/qq/message-templates/{first['id']}")).json()[
        "message_template"
    ] == "{text}"


async def test_apply_checks_placeholders_again(context):
    client, db = context
    first = (
        await client.post(
            "/qq/message-templates", json={"name": "模板", "message_template": "{text}"}
        )
    ).json()
    await db.delete(await db.get(QQPlaceholder, 3))
    await db.commit()
    response = await client.get(f"/qq/message-templates/{first['id']}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "qq_template_unknown"
    # List remains readable so the saved template can be repaired.
    assert (await client.get("/qq/message-templates")).status_code == 200
