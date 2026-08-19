"""FortyGuard API client: submit -> activity_id -> poll /v1/status/{id}.

Uses the official `fortyguard` client from FortyGuard's quickstart template
when it is importable, and falls back to a minimal built-in implementation
otherwise, so nothing is blocked on getting the template vendored in. Either
way the disk cache wraps it -- the cache is ours and is not optional.

Facts this encodes, from the handbook and the API docs:
  * Every endpoint is asynchronous. POST returns data.activity_id; you then
    poll GET /v1/status/{activity_id} until status is Completed or Failed.
  * `Failed` is TERMINAL. Do not retry it. Retrying a Failed activity is how
    people burn an afternoon on a request that was malformed to begin with.
  * Failed and rejected tasks are FREE -- credits are deducted only on
    success. That inverts the usual risk model: validate request shape on a
    tiny polygon freely, and spend your caution on *successful* calls against
    the wrong AOI or an unnecessarily fine grid.
  * Status strings are matched case-insensitively (Completed / completed /
    succeeded all appear in the wild).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

from .cache import ResponseCache

BASE_URL = "https://api.fortyguard.com/v1"

_DONE = {"completed", "succeeded", "success"}
_FAILED = {"failed", "error"}


class FortyGuardError(RuntimeError):
    """A call failed. Carries the activity_id so it can be reported."""

    def __init__(self, message: str, activity_id: Optional[str] = None,
                 status_code: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.activity_id = activity_id
        self.status_code = status_code
        self.body = body


class Rejected(FortyGuardError):
    """Submit-time rejection (4xx). Free. Usually a malformed request."""


@dataclass
class CallRecord:
    endpoint: str
    activity_id: Optional[str]
    latency_s: float
    from_cache: bool
    ok: bool
    note: str = ""


@dataclass
class NightwatchClient:
    api_key: Optional[str] = None
    base_url: str = BASE_URL
    cache_path: Path | str = "data/cache.sqlite"
    use_cache: bool = True
    poll_initial_s: float = 2.0
    poll_max_s: float = 15.0
    poll_backoff: float = 1.5
    timeout_s: float = 900.0
    # how many consecutive 5xx / connection failures to absorb before
    # concluding the activity is genuinely unreachable
    max_transient_failures: int = 8
    calls: list = field(default_factory=list)

    def __post_init__(self):
        self.api_key = self.api_key or os.environ.get("FORTYGUARD_API_KEY")
        if not self.api_key:
            raise SystemExit(
                "No API key. Copy .env.example to .env, put your key in "
                "FORTYGUARD_API_KEY, and make sure load_dotenv() has run."
            )
        self.cache = ResponseCache(self.cache_path)
        self._session = requests.Session()
        self._session.headers.update({"api-key": self.api_key})

        # Prefer FortyGuard's own client if the quickstart template is vendored.
        try:
            from fortyguard import FortyGuardClient  # type: ignore
            self._official = FortyGuardClient()
            self.using_official = True
        except Exception:
            self._official = None
            self.using_official = False

    # ----------------------------------------------------------------- core
    def call(
        self,
        endpoint: str,
        payload: dict,
        *,
        bypass_cache: bool = False,
        timeout_s: Optional[float] = None,
    ) -> Any:
        """Submit, poll, return `result`. Cached by request-body hash."""
        if self.use_cache and not bypass_cache:
            hit = self.cache.get(endpoint, payload)
            if hit is not None:
                self.calls.append(
                    CallRecord(endpoint, None, 0.0, True, True, "cache hit")
                )
                return hit

        started = time.monotonic()
        activity_id = self.submit(endpoint, payload)
        result = self.wait_for(activity_id, timeout_s=timeout_s or self.timeout_s)
        latency = time.monotonic() - started

        if self.use_cache:
            self.cache.put(endpoint, payload, result, activity_id, latency)
        self.calls.append(
            CallRecord(endpoint, activity_id, latency, False, True)
        )
        return result

    def submit(self, endpoint: str, payload: dict) -> str:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        r = self._session.post(url, json=payload, timeout=60)
        if r.status_code >= 400:
            # Rejections are free. Surface the body -- it usually names the
            # offending field, which is the fastest debugging signal you get.
            raise Rejected(
                f"{endpoint} rejected with HTTP {r.status_code}: {r.text[:400]}",
                status_code=r.status_code,
                body=_safe_json(r),
            )
        body = r.json()
        try:
            return body["data"]["activity_id"]
        except (KeyError, TypeError):
            raise FortyGuardError(
                f"{endpoint} returned no activity_id: {body}", body=body
            )

    def get_status(self, activity_id: str) -> Optional[dict]:
        """Poll one activity.

        Returns None on a TRANSIENT failure (5xx, connection reset, read
        timeout) rather than raising. The distinction matters: a 504 from
        the gateway says nothing about the activity, which is very probably
        still running happily server-side. Treating it as fatal is how one
        blip kills a 24-call sweep.

        A 4xx, by contrast, means the activity_id is bad -- that is real and
        does raise.
        """
        try:
            r = self._session.get(
                f"{self.base_url}/status/{activity_id}", timeout=60
            )
        except (requests.ConnectionError, requests.Timeout):
            return None

        if r.status_code >= 500:
            return None
        if r.status_code >= 400:
            raise FortyGuardError(
                f"status lookup for {activity_id} returned HTTP "
                f"{r.status_code}: {r.text[:300]}",
                activity_id=activity_id,
                status_code=r.status_code,
            )
        try:
            return r.json()["data"]
        except (ValueError, KeyError):
            return None  # malformed body -- treat as transient

    def wait_for(self, activity_id: str, timeout_s: Optional[float] = None) -> Any:
        deadline = time.monotonic() + (timeout_s or self.timeout_s)
        delay = self.poll_initial_s
        consecutive_transient = 0

        while time.monotonic() < deadline:
            data = self.get_status(activity_id)

            if data is None:
                consecutive_transient += 1
                if consecutive_transient >= self.max_transient_failures:
                    raise FortyGuardError(
                        f"activity {activity_id}: "
                        f"{consecutive_transient} consecutive transient "
                        f"status failures -- giving up",
                        activity_id=activity_id,
                    )
                # back off harder on server trouble than on normal polling
                time.sleep(min(delay * 2, self.poll_max_s))
                delay = min(delay * self.poll_backoff, self.poll_max_s)
                continue

            consecutive_transient = 0
            status = str(data.get("status", "")).strip().lower()

            if status in _DONE:
                return data.get("result")
            if status in _FAILED:
                # TERMINAL. Retrying is pointless and the handbook says so.
                raise FortyGuardError(
                    f"activity {activity_id} reported {data.get('status')!r} "
                    f"-- terminal, not retrying",
                    activity_id=activity_id,
                    body=data,
                )
            time.sleep(delay)
            delay = min(delay * self.poll_backoff, self.poll_max_s)

        raise TimeoutError(
            f"activity {activity_id} did not complete within "
            f"{timeout_s or self.timeout_s}s"
        )

    # ------------------------------------------------------------ endpoints
    def usage(self) -> dict:
        """POST /v1/system/fetch-api-key-usage -- credit balance and cycle usage.

        Synchronous in practice (no activity_id), so it is handled directly.

        Note: this endpoint wants the key in the JSON **body** as `api_key`,
        not only in the `api-key` header like every other endpoint. It
        returns 422 "Field 'api_key' is required" otherwise. We send both.
        """
        r = self._session.post(
            f"{self.base_url}/system/fetch-api-key-usage",
            json={"api_key": self.api_key},
            timeout=60,
        )
        if r.status_code >= 400:
            raise Rejected(
                f"usage lookup failed HTTP {r.status_code}: {r.text[:400]}",
                status_code=r.status_code,
                body=_safe_json(r),
            )
        return r.json()

    def heatmap(
        self,
        polygon_aoi: dict,
        start_date: str,
        *,
        filter_type: int = 3,
        granularity: int = 80,
        analytic_type: str = "tcm",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        end_date: Optional[str] = None,
        threshold: Optional[float] = None,
        direction: Optional[str] = None,
        **kw,
    ) -> Any:
        date_time: dict = {"start_date": start_date, "filter_type": filter_type}
        if start_time is not None:
            date_time["start_time"] = start_time
        if end_time is not None:
            date_time["end_time"] = end_time
        if end_date is not None:
            date_time["end_date"] = end_date

        payload: dict = {
            "polygon_aoi": polygon_aoi,
            "date_time": date_time,
            "granularity": granularity,
            "analytic_type": analytic_type,
        }
        # threshold/direction are required for exceedance and persistence,
        # and ignored by tcm and time_of_measure.
        if analytic_type in ("exceedance", "persistence"):
            payload["threshold"] = 30 if threshold is None else threshold
            payload["direction"] = direction or "above"
        return self.call("heatmap", payload, **kw)

    def env_params(
        self,
        latitude: float,
        longitude: float,
        temperature: float,
        start_date: str,
        *,
        filter_type: int = 1,
        start_time: Optional[str] = "14:00",
        end_time: Optional[str] = None,
        analysis: Optional[list] = None,
        **kw,
    ) -> Any:
        date_time: dict = {"start_date": start_date, "filter_type": filter_type}
        if start_time is not None:
            date_time["start_time"] = start_time
        if end_time is not None:
            date_time["end_time"] = end_time

        payload: dict = {
            "latitude": latitude,
            "longitude": longitude,
            "temperature": temperature,
            "date_time": date_time,
        }
        if analysis:
            payload["analysis"] = analysis
        return self.call("env_params", payload, **kw)

    def satellite(
        self,
        latitude: float,
        longitude: float,
        start_date: str,
        *,
        start_time: str = "14:00",
        filter_type: int = 1,
        granularity: int = 80,
        **kw,
    ) -> Any:
        payload = {
            "sat": {"latitude": latitude, "longitude": longitude},
            "date_time": {
                "start_date": start_date,
                "start_time": start_time,
                "filter_type": filter_type,
            },
            "granularity": granularity,
        }
        return self.call("satellite", payload, **kw)

    def streetview(
        self,
        latitude: float,
        longitude: float,
        *,
        vertical_angle: float = 15.0,
        horizontal_angle: float = 0.0,
        back_view: bool = False,
        **kw,
    ) -> Any:
        payload = {
            "latitude": latitude,
            "longitude": longitude,
            "vertical_angle": vertical_angle,
            "horizontal_angle": horizontal_angle,
            "back_view": back_view,
        }
        return self.call("streetview", payload, **kw)


def _safe_json(resp) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text[:1000]
