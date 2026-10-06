"""Thin HTTP client for the Elexon Insights API.

The Insights API is public and keyless. Every request made here carries only
public route names and date parameters.

API documentation: https://developer.data.elexon.co.uk/
"""
from __future__ import annotations

import time

import requests

DEFAULT_BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"

# Transient failures worth retrying; any other 4xx/5xx fails immediately.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class ElexonApiError(RuntimeError):
    """Raised when the Elexon API cannot be reached or returns an error."""

    def __init__(
        self,
        message: str,
        *,
        url: str | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class ElexonClient:
    """Small wrapper around ``requests`` with retries and consistent errors.

    Kept free of UI and caching concerns.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        session: requests.Session | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._session = session or requests.Session()

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET a JSON payload from the API, retrying transient failures.

        Connection errors, timeouts, and 429/5xx responses are retried with
        exponential backoff up to ``max_attempts``. Any other error response
        raises :class:`ElexonApiError` immediately.
        """
        url = f"{self._base_url}/{path.lstrip('/')}"
        last_error: ElexonApiError | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._session.get(
                    url,
                    params=params,
                    timeout=self._timeout_seconds,
                    headers={"Accept": "application/json"},
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = ElexonApiError(
                    f"Could not reach the Elexon API at {url}: {exc}", url=url
                )
            else:
                if response.status_code in RETRYABLE_STATUS_CODES:
                    last_error = ElexonApiError(
                        f"Elexon API returned HTTP {response.status_code} for {url}",
                        url=url,
                        status_code=response.status_code,
                    )
                elif response.status_code >= 400:
                    raise ElexonApiError(
                        f"Elexon API request failed with HTTP {response.status_code} for {url}",
                        url=url,
                        status_code=response.status_code,
                    )
                else:
                    return response.json()

            if attempt < self._max_attempts:
                time.sleep(self._backoff_seconds * 2 ** (attempt - 1))

        assert last_error is not None  # loop always sets it before falling through
        raise last_error
