"""Request bodies (Pydantic v2). Secrets are SecretStr so they never end up in logs or reprs."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(StrictModel):
    password: SecretStr


class PasswordChangeIn(StrictModel):
    current: SecretStr
    new: SecretStr


class AppSettingsIn(StrictModel):
    claude_api_key: SecretStr | None = None  # write-only; empty string removes it
    claude_model: str | None = Field(None, max_length=80)
    ui_refresh_seconds: int | None = Field(None, ge=2, le=600)


class AccountCreateIn(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(min_length=3, max_length=40)
    color: str | None = None


class AccountUpdateIn(StrictModel):
    name: str | None = Field(None, max_length=80)
    color: str | None = None
    settings: dict[str, Any] | None = None
    automation_paused: bool | None = None


class TelegramTokenIn(StrictModel):
    token: SecretStr


class BotConnectionIn(StrictModel):
    base_url: str = Field(max_length=300)
    username: str = Field("", max_length=64)
    api_key: SecretStr | None = None  # omit to keep the stored key


class SyncIn(StrictModel):
    kind: Literal["fast", "stats", "bot", "all"] = "all"


class AdActionIn(StrictModel):
    action: Literal["pause", "resume", "withdraw", "topup", "submit_review"]
    amount: float | None = Field(None, ge=0)


class AdEditIn(StrictModel):
    text: str | None = Field(None, max_length=400)
    tag_id: int | None = None
    variant_id: int | None = None
    note: str | None = Field(None, max_length=500)
    clear_tag: bool = False


class AdCreateIn(StrictModel):
    kind: Literal["channel", "bot"]
    username: str = Field(max_length=40)
    text: str = Field(max_length=400)
    cpm: float = Field(gt=0)
    budget: float = Field(gt=0)
    title: str | None = Field(None, max_length=120)
    variant_id: int | None = None
    tag_id: int | None = None
    show_userpic: bool = False
    note: str | None = Field(None, max_length=500)
    replace_ad_id: int | None = None


class ApproveKindIn(StrictModel):
    kind: Literal["stop", "withdraw", "topup", "refill"]


class TextIn(StrictModel):
    name: str = Field(max_length=80)
    text: str = Field(max_length=400)


class TextUpdateIn(StrictModel):
    name: str | None = Field(None, max_length=80)
    text: str | None = Field(None, max_length=400)
    active: bool | None = None


class TextAnalyzeIn(StrictModel):
    goal: str = Field("", max_length=500)


class TagIn(StrictModel):
    name: str = Field(max_length=80)
    referrer_id: str = Field(max_length=32)
    start_code: str = Field("", max_length=64)
    note: str = Field("", max_length=500)


class TagUpdateIn(StrictModel):
    name: str | None = Field(None, max_length=80)
    referrer_id: str | None = Field(None, max_length=32)
    start_code: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=500)


class ReferrerLookupIn(StrictModel):
    referrer_id: str = Field(max_length=32)


class AnalysisIn(StrictModel):
    question: str = Field("", max_length=2000)


class KillIn(StrictModel):
    confirm: str


class PerAdText(StrictModel):
    text: str = Field(max_length=400)


class EditProbeIn(StrictModel):
    ad_id: int
    text: str = Field(min_length=1, max_length=400)
    confirm: bool = False


class BulkIn(StrictModel):
    kind: Literal["content", "referral", "replace"]
    ad_ids: list[int] = Field(min_length=1, max_length=500)
    mode: Literal["replace", "inplace"] = "replace"
    text: str | None = Field(None, max_length=400)
    variant_id: int | None = None
    per_ad: dict[int, PerAdText] | None = None
    photo_id: str | None = Field(None, max_length=200)
    photo_name: str | None = Field(None, max_length=120)
    budget_source: Literal["account", "transfer"] = "transfer"
    initial_budget: float | None = Field(None, ge=0)
    topup_after_withdraw: bool = False
    group_by: Literal["target", "cpm", "placement"] = "target"
    referral_mode: Literal["pool", "shared", "random"] = "pool"
    shared_referrer_id: str | None = Field(None, max_length=32)
    shared_tag_id: int | None = None
    proposal_ids: list[int] | None = None
    confirm: bool = False


class ResubmitIn(StrictModel):
    text: str = Field(max_length=400)


class TextCheckIn(StrictModel):
    text: str = Field(max_length=1000)
    promote_url: str | None = Field(None, max_length=300)
    ad_id: int | None = None


class TextFixIn(StrictModel):
    text: str = Field(max_length=1000)
    reason: str = Field("", max_length=1000)
    ad_id: int | None = None
