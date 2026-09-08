from pydantic import BaseModel, ConfigDict, Field


class NacosHost(BaseModel):
    ip: str
    port: int = Field(ge=1, le=65535)
    healthy: bool = True
    enabled: bool = True


class NacosInstanceList(BaseModel):
    hosts: list[NacosHost] = Field(default_factory=list)


class ServiceTokenResponse(BaseModel):
    access_token: str = Field(min_length=1)
    token_type: str = "Bearer"
    expires_in: int = Field(gt=0)


class ResourceInstance(BaseModel):
    model_config = ConfigDict(extra="allow")

    service_id: str
    instance_id: str
    name: str
    component: str
    node: str
    status: str
    cpu_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    memory_percent: float | None = None
    sampled_at: str | None = None


class ResourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    sampled_at: str
    instances: list[ResourceInstance] = Field(default_factory=list)
    node: str | None = None
    mode: str | None = None
    stale_seconds: float | None = None
    error: str | None = None
