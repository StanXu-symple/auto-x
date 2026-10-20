"""Allowlisted Skill API facade; contracts come from the owning service routes."""

import inspect
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute

from app.services.skill_client import get_skill_client
from app.skills import listening_routes, routes


def proxy_router(source: APIRouter, *, strip_prefix: str = "") -> APIRouter:
    router = APIRouter()
    for route in source.routes:
        if not isinstance(route, APIRoute):
            continue

        def make_endpoint(contract: APIRoute):
            async def forward(request: Request, **_validated):
                path = contract.path
                for name, value in request.path_params.items():
                    path = path.replace("{" + name + "}", quote(str(value), safe=""))
                if not path.startswith("/ai/"):
                    path = "/ai" + path
                headers = {
                    "X-User-Authorization": request.headers.get("authorization", ""),
                    "X-Request-ID": getattr(request.state, "request_id", ""),
                    "Content-Type": request.headers.get("content-type", "application/json"),
                }
                result = await get_skill_client().request(
                    request.method,
                    path,
                    headers=headers,
                    params=list(request.query_params.multi_items()),
                    content=await request.body(),
                )
                return Response(
                    content=result.content,
                    status_code=result.status_code,
                    headers={
                        key: value
                        for key, value in result.headers.items()
                        if key in {"content-type", "www-authenticate", "retry-after"}
                    },
                )

            # Keep public body/query/path models and real admin authentication,
            # without opening the handler's DB/Redis dependencies or invoking it.
            signature = inspect.signature(contract.endpoint, eval_str=True)
            forward.__signature__ = signature.replace(
                parameters=[
                    inspect.Parameter(
                        "request",
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=Request,
                    )
                ]
                + [
                    parameter.replace(kind=inspect.Parameter.KEYWORD_ONLY)
                    for name, parameter in signature.parameters.items()
                    if name not in {"db", "redis"}
                ],
            )
            forward.__name__ = contract.name
            return forward

        router.add_api_route(
            route.path.removeprefix(strip_prefix),
            make_endpoint(route),
            methods=sorted(route.methods),
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            name=route.name,
        )
    return router


management_router = proxy_router(routes.router, strip_prefix="/ai")
listening_router = proxy_router(listening_routes.router)
