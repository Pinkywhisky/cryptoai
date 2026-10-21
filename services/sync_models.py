"""Strict payloads for explicit historical import and incremental background jobs."""
from typing import Annotated, Literal
from pydantic import Field, StrictBool, field_validator, model_validator
from services.api_models import Payload

JobId=Annotated[str,Field(strict=True,pattern=r"^[a-f0-9]{32}$")]
Market=Annotated[str,Field(strict=True,min_length=1,max_length=40,pattern=r"^[A-Z0-9][A-Z0-9._-]*$")]


class SyncPreview(Payload):
    symbols: Annotated[list[Market],Field(max_length=3000)] = Field(default_factory=list)
    full_scan: StrictBool = False

    @field_validator("symbols",mode="before")
    @classmethod
    def normalize_symbols(cls,value):
        return [s.strip().upper() if isinstance(s,str) else s for s in value] if isinstance(value,list) else value


class SyncStart(Payload):
    mode: Literal["incremental","bootstrap"] = "incremental"
    preview_id: JobId | None = None
    confirmed: StrictBool = False
    resume_id: JobId | None = None

    @model_validator(mode="after")
    def valid_combination(self):
        if self.resume_id:
            if self.mode!="incremental" or self.preview_id or self.confirmed:
                raise ValueError("Resume identifies its stored mode")
        elif self.mode=="bootstrap":
            if not self.preview_id:
                raise ValueError("Bootstrap preview required")
        elif self.preview_id or self.confirmed:
            raise ValueError("Preview is only valid for bootstrap")
        return self
