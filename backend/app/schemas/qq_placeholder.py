from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import APIModel
from app.schemas.tweet import TweetOut

FIELD_LABELS = {
    "id": "内容记录 ID",
    "tweet_id": "X 推文 ID",
    "monitored_user_id": "监听账号 ID",
    "username": "账号（不含 @）",
    "display_name": "昵称（为空时使用账号）",
    "author_id": "X 作者 ID",
    "tweet_type": "内容类型（original/reply/retweet）",
    "text": "正文",
    "lang": "语言",
    "conversation_id": "会话 ID",
    "posted_at": "发布时间（UTC）",
    "fetched_at": "采集时间（UTC）",
    "like_count": "点赞数",
    "retweet_count": "转推数",
    "reply_count": "回复数",
    "quote_count": "引用数",
    "bookmark_count": "收藏数",
    "impression_count": "浏览数",
    "entities": "实体信息（JSON）",
    "attachments": "附件信息（JSON）",
    "referenced_tweets": "引用推文（JSON）",
    "raw_payload": "原始数据（JSON）",
    "url": "系统生成：原文链接",
    "title": "系统生成：默认推送标题",
}
SOURCE_FIELDS = set(TweetOut.model_fields) | {"url", "title"}
DEFAULT_PLACEHOLDERS = [
    {"placeholder": "{author}", "source_field": "display_name"},
    {"placeholder": "{username}", "source_field": "username"},
    {"placeholder": "{text}", "source_field": "text"},
    {"placeholder": "{url}", "source_field": "url"},
    {"placeholder": "{posted_at}", "source_field": "posted_at"},
    {"placeholder": "{title}", "source_field": "title"},
]


class QQPlaceholderWrite(APIModel):
    placeholder: str = Field(pattern=r"^\{[a-z][a-z0-9_]{0,63}\}$")
    source_field: str

    @field_validator("source_field")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if value not in SOURCE_FIELDS:
            raise ValueError("请选择内容流字段或系统生成字段")
        return value


class QQPlaceholderOut(QQPlaceholderWrite):
    id: int
    created_at: datetime
    updated_at: datetime


class QQPlaceholderField(APIModel):
    value: str
    label: str
    category: str
