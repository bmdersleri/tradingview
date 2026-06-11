"""Exception hierarchy and exit-code mapping."""

from __future__ import annotations

from dataclasses import dataclass

EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_USAGE = 2
EXIT_AUTH_REQUIRED = 3
EXIT_NOT_FOUND = 4
EXIT_RATE_LIMITED = 5
EXIT_UPSTREAM_CHANGED = 6
EXIT_NETWORK = 7
EXIT_BROWSER = 8


@dataclass(slots=True)
class ErrorPayload:
    code: str
    message: str
    retryable: bool
    hint: str


class TvcliError(Exception):
    """Base error for tvcli."""

    code = "GENERIC"
    retryable = False
    hint = "Inspect the request and try again."
    exit_code = EXIT_GENERIC

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint

    def to_payload(self) -> ErrorPayload:
        return ErrorPayload(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            hint=self.hint,
        )


class UsageError(TvcliError):
    code = "USAGE"
    exit_code = EXIT_USAGE
    hint = "Fix the command syntax and try again."


class AuthRequiredError(TvcliError):
    code = "AUTH_REQUIRED"
    exit_code = EXIT_AUTH_REQUIRED
    hint = "Run `tvcli auth login` and retry."


class SessionRequiredError(AuthRequiredError):
    code = "SESSION_REQUIRED"
    hint = "Run `tvcli auth import-cookie` or `tvcli auth login` first."


class SessionExpiredError(AuthRequiredError):
    code = "SESSION_EXPIRED"
    hint = "Refresh the TradingView session with `tvcli auth login`."


class CaptchaDetectedError(AuthRequiredError):
    code = "CAPTCHA_DETECTED"
    hint = (
        "Use `tvcli auth import-cookie` on a browser where you already solved "
        "the challenge."
    )


class NotFoundError(TvcliError):
    code = "NOT_FOUND"
    exit_code = EXIT_NOT_FOUND
    hint = "Check the symbol or resource identifier and try again."


class RateLimitedError(TvcliError):
    code = "RATE_LIMITED"
    retryable = True
    exit_code = EXIT_RATE_LIMITED
    hint = "Retry after a short backoff."


class UpstreamChangedError(TvcliError):
    code = "UPSTREAM_CHANGED"
    exit_code = EXIT_UPSTREAM_CHANGED
    hint = "Inspect the upstream contract and update the adapter."


class NetworkError(TvcliError):
    code = "NETWORK"
    retryable = True
    exit_code = EXIT_NETWORK
    hint = "Check connectivity and retry."


class BrowserError(TvcliError):
    code = "BROWSER"
    exit_code = EXIT_BROWSER
    hint = "Inspect the browser session, selector, or login state."


def error_to_payload(error: TvcliError) -> dict[str, object]:
    payload = error.to_payload()
    return {
        "code": payload.code,
        "message": payload.message,
        "retryable": payload.retryable,
        "hint": payload.hint,
    }
