# ruff: noqa: B008, E501
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...config import load_config, resolve_setting, save_config

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None
    low_float_threshold: float = Field(20.0, ge=0.0, le=100.0)
    severe_low_float_threshold: float = Field(10.0, ge=0.0, le=100.0)
    ratio_jump_threshold: float = Field(5.0, ge=0.0, le=100.0)


class SettingsTest(BaseModel):
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_url: str | None = None


@router.get("")
async def get_settings() -> Any:
    try:
        cfg = load_config()
        token = resolve_setting("alerts", "telegram-token", cfg, "")
        chat_id = resolve_setting("alerts", "telegram-chat-id", cfg, "")
        webhook_url = resolve_setting("alerts", "webhook-url", cfg, "")
        low_float = resolve_setting("alerts", "low-float-threshold", cfg, 20.0)
        severe_low_float = resolve_setting(
            "alerts", "severe-low-float-threshold", cfg, 10.0
        )
        ratio_jump = resolve_setting("alerts", "ratio-jump-threshold", cfg, 5.0)

        masked_token = ""
        if token:
            if len(token) > 8:
                masked_token = f"{token[:4]}****************{token[-4:]}"
            else:
                masked_token = "****************"

        return {
            "telegram_token": masked_token,
            "telegram_chat_id": chat_id,
            "webhook_url": webhook_url,
            "low_float_threshold": low_float,
            "severe_low_float_threshold": severe_low_float,
            "ratio_jump_threshold": ratio_jump,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@router.post("/update")
async def update_settings(payload: SettingsUpdate) -> Any:
    try:
        cfg = load_config()

        if "alerts" not in cfg:
            cfg["alerts"] = {}

        token = payload.telegram_token
        if token and "*" in token:
            token = resolve_setting("alerts", "telegram-token", cfg, "")

        cfg["alerts"]["telegram-token"] = token or ""
        cfg["alerts"]["telegram-chat-id"] = payload.telegram_chat_id or ""
        cfg["alerts"]["webhook-url"] = payload.webhook_url or ""
        cfg["alerts"]["low-float-threshold"] = payload.low_float_threshold
        cfg["alerts"]["severe-low-float-threshold"] = payload.severe_low_float_threshold
        cfg["alerts"]["ratio-jump-threshold"] = payload.ratio_jump_threshold

        save_config(cfg)
        return {"status": "success", "message": "Settings updated successfully."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        ) from e


@router.post("/test")
async def test_settings(payload: SettingsTest) -> Any:
    errors = []

    token = payload.telegram_token
    if token and "*" in token:
        cfg = load_config()
        token = resolve_setting("alerts", "telegram-token", cfg, "")

    chat_id = payload.telegram_chat_id
    webhook_url = payload.webhook_url

    if token and chat_id:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            text = "⚡ <b>tvcli Test Alarmı:</b> Telegram bildirim kanalı bağlantısı başarıyla doğrulandı! ✅"
            res = httpx.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=5.0,
            )
            if res.status_code != 200:
                errors.append(
                    f"Telegram API returned status {res.status_code}: {res.text}"
                )
        except Exception as e:
            errors.append(f"Telegram connection error: {str(e)}")

    if webhook_url:
        try:
            test_payload = {
                "report_date": "TEST_DATE",
                "events": [
                    {
                        "code": "TEST",
                        "event_type": "test_alert",
                        "severity": "info",
                        "metric_value": 0.0,
                        "threshold_value": 0.0,
                        "payload": {
                            "message": "tvcli Test Alarmı: Webhook bağlantısı başarıyla doğrulandı! ✅"
                        },
                    }
                ],
            }
            res = httpx.post(webhook_url, json=test_payload, timeout=5.0)
            if res.status_code not in (200, 201, 204):
                errors.append(f"Webhook returned status {res.status_code}: {res.text}")
        except Exception as e:
            errors.append(f"Webhook connection error: {str(e)}")

    if not token and not chat_id and not webhook_url:
        return {
            "status": "error",
            "message": "No alert channel configured to test.",
        }

    if errors:
        return {"status": "error", "message": "; ".join(errors)}

    return {"status": "success", "message": "Test notification sent successfully."}
