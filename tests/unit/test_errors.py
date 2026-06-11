from tvcli.errors import (
    AuthRequiredError,
    CaptchaDetectedError,
    RateLimitedError,
    SessionExpiredError,
    SessionRequiredError,
    TvcliError,
    error_to_payload,
)


def test_base_error_payload_contains_required_fields() -> None:
    error = TvcliError("boom")

    payload = error_to_payload(error)

    assert payload["code"] == "GENERIC"
    assert payload["retryable"] is False
    assert "hint" in payload


def test_subclass_exit_code_and_retryable_flag() -> None:
    error = RateLimitedError("slow down")

    assert error.exit_code == 5
    assert error.retryable is True
    assert (
        error_to_payload(AuthRequiredError("missing session"))["code"]
        == "AUTH_REQUIRED"
    )
    assert (
        error_to_payload(SessionRequiredError("missing"))["code"] == "SESSION_REQUIRED"
    )
    assert error_to_payload(SessionExpiredError("expired"))["code"] == "SESSION_EXPIRED"
    assert (
        error_to_payload(CaptchaDetectedError("captcha"))["code"] == "CAPTCHA_DETECTED"
    )
