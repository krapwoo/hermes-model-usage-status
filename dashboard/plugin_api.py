from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from hermes_constants import get_hermes_home  # noqa: E402
from model_usage_status.service import (  # noqa: E402
    AuthenticationNotRequired,
    AuthenticationUnavailable,
    UsageService,
)

router = APIRouter()
service = UsageService(get_hermes_home() / "plugin-data" / "model-usage-status")


@router.get("/usage")
async def usage() -> dict:
    return await run_in_threadpool(service.get)


@router.post("/refresh")
async def refresh() -> dict:
    return await run_in_threadpool(service.refresh)


@router.post("/authentication/{provider}", status_code=status.HTTP_202_ACCEPTED)
async def reauthenticate(provider: Literal["claude", "codex"]) -> dict:
    try:
        return await run_in_threadpool(service.launch_reauthentication, provider)
    except AuthenticationNotRequired as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except AuthenticationUnavailable as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
