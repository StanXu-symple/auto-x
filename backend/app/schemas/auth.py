from pydantic import Field

from app.schemas.common import APIModel


class LoginRequest(APIModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AdminPublic(APIModel):
    id: int
    username: str
    is_active: bool


class TokenResponse(APIModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AdminPublic
