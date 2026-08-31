from datetime import datetime
from typing import Any

from app.schemas.common import APIModel
from app.schemas.polling import PollingLogOut
from app.schemas.tweet import TweetOut


class CountSummary(APIModel):
    monitored_users: int
    active_users: int
    paused_users: int
    tweets: int
    tweets_last_24h: int


class PollingSummary(APIModel):
    runs_last_24h: int
    successful_runs_last_24h: int
    failed_runs_last_24h: int
    success_rate: float
    due_users: int


class DashboardSummary(APIModel):
    generated_at: datetime
    counts: CountSummary
    polling: PollingSummary
    server: dict[str, Any]
    recent_tweets: list[TweetOut]
    recent_runs: list[PollingLogOut]
