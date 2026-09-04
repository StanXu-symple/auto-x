from fastapi import APIRouter

from app.api.routes import (
    ai,
    ai_data_source,
    auth,
    dashboard,
    health,
    monitored_users,
    polling_logs,
    qq,
    settings,
    system,
    tweets,
    x_credentials,
    x_sources,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(monitored_users.router, prefix="/monitored-users")
api_router.include_router(monitored_users.router, prefix="/accounts", include_in_schema=False)
api_router.include_router(tweets.router, prefix="/tweets")
api_router.include_router(tweets.router, prefix="/posts", include_in_schema=False)
api_router.include_router(ai.tweets_router)
api_router.include_router(ai.router)
api_router.include_router(ai_data_source.router)
api_router.include_router(polling_logs.router, prefix="/polling-logs")
api_router.include_router(polling_logs.router, prefix="/poll-runs", include_in_schema=False)
api_router.include_router(qq.router)
api_router.include_router(system.router)
api_router.include_router(settings.router)
api_router.include_router(x_credentials.router)
api_router.include_router(x_sources.router)
api_router.include_router(health.router)
