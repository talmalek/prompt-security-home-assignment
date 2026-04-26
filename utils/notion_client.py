"""Async Notion API wrapper (httpx + tenacity + pydantic).

Keeps test code and CI scripts free of raw HTTP — callers only see typed
methods and pydantic models. Also serves as the reference pattern for any
future external-API wrapper in this repo (retries on transport + 429/5xx,
`SecretStr` token, context-manager lifecycle, pydantic payload models).

Scope is intentionally narrow — only what the reporter needs:
    - `retrieve_database` for pre-flight schema validation
    - `create_run_row` to post a single summary row per CI run

Extend here if future work needs query / patch / block endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import settings
from utils.logger import logger

NOTION_API_BASE = "https://api.notion.com/v1"

RunStatus = Literal["✅ Done", "⚠️ Known failures", "❌ Failed"]

STATUS_DONE: Final[RunStatus] = "✅ Done"
STATUS_KNOWN_FAILURES: Final[RunStatus] = "⚠️ Known failures"
STATUS_FAILED: Final[RunStatus] = "❌ Failed"

STATUS_LEGACY_MAP: Final[dict[str, RunStatus]] = {
    "passed": STATUS_DONE,
    "partial": STATUS_KNOWN_FAILURES,
    "failed": STATUS_FAILED,
}


class NotionApiError(Exception):
    """Raised when Notion returns a non-retryable error response."""


class TestRunRow(BaseModel):
    """Typed payload for a single 'Test Runs' row.

    Pydantic model instead of a raw dict so the CI script has a clear contract
    and any future schema change produces a visible type error.
    """

    model_config = ConfigDict(frozen=True)

    run_title: str
    status: RunStatus
    started_at: datetime
    duration_seconds: float
    total: int
    passed: int
    failed: int
    skipped: int
    branch: str
    commit: str
    ci_run_url: str | None = None
    allure_report_url: str | None = None
    triggered_by: str = ""

    def to_notion_properties(self) -> dict[str, Any]:
        """Translate this row into Notion's property-value shape.

        Property names must match the database schema exactly
        (see scripts/smoke_notion.py for the contract).
        """
        props: dict[str, Any] = {
            "Run": {"title": [{"text": {"content": self.run_title}}]},
            "Status": {"select": {"name": self.status}},
            "Started": {"date": {"start": self.started_at.isoformat()}},
            "Duration (s)": {"number": round(self.duration_seconds, 2)},
            "Total": {"number": self.total},
            "Passed": {"number": self.passed},
            "Failed": {"number": self.failed},
            "Skipped": {"number": self.skipped},
            "Branch": {"rich_text": [{"text": {"content": self.branch}}]},
            "Commit": {"rich_text": [{"text": {"content": self.commit}}]},
            "Triggered by": {"rich_text": [{"text": {"content": self.triggered_by}}]},
        }
        if self.ci_run_url:
            props["CI Run"] = {"url": self.ci_run_url}
        if self.allure_report_url:
            props["Allure Report"] = {"url": self.allure_report_url}
        return props


def _is_retryable(exc: BaseException) -> bool:
    """Retry on transport issues and transient HTTP errors (429, 5xx)."""
    if isinstance(exc, httpx.TransportError | httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    return False


class NotionClient:
    """Minimal async Notion API client.

    Usage:
        async with NotionClient(token=..., api_version=...) as client:
            await client.retrieve_database(db_id)
            await client.create_run_row(db_id, TestRunRow(...))

    The context manager ensures the underlying httpx client is closed even
    if a request raises.
    """

    def __init__(
        self,
        token: SecretStr,
        *,
        api_version: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._token = token
        self._api_version = api_version or settings.notion.api_version
        self._timeout = timeout_seconds if timeout_seconds is not None else settings.notion.timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=NOTION_API_BASE,
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._token.get_secret_value()}",
                "Notion-Version": self._api_version,
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        retry=retry_if_exception(_is_retryable),
    )
    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        logger.info("Notion request", method=method, path=path)
        response = await self._client.request(method, path, json=json)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            logger.warning("Notion HTTP error", status=exc.response.status_code, body=body)
            if not _is_retryable(exc):
                raise NotionApiError(f"Notion {method} {path} failed: {exc.response.status_code} {body}") from exc
            raise
        return response.json()

    async def retrieve_database(self, database_id: str) -> dict[str, Any]:
        """GET /v1/databases/{id} — used for pre-flight schema checks."""
        return await self._request("GET", f"/databases/{database_id}")

    async def create_run_row(self, database_id: str, row: TestRunRow) -> str:
        """POST /v1/pages — creates one row in the given database. Returns page ID."""
        payload = {
            "parent": {"database_id": database_id},
            "properties": row.to_notion_properties(),
        }
        data = await self._request("POST", "/pages", json=payload)
        page_id = str(data.get("id", ""))
        logger.info("Notion row created", page_id=page_id, status=row.status, title=row.run_title)
        return page_id


class TestRunRowBuilder(BaseModel):
    """Helper to derive a `TestRunRow` from a raw summary.json dict.

    Keeps the CI script thin — it just hands us the JSON plus env vars and we
    do the shaping here where it can be unit-tested if needed later.
    """

    model_config = ConfigDict(extra="ignore")

    summary: dict[str, Any]
    branch: str = "local"
    commit: str = "local"
    run_number: str = "local"
    ci_run_url: str | None = None
    allure_report_url: str | None = None
    triggered_by: str = ""

    def build(self) -> TestRunRow:
        s = self.summary
        total = int(s.get("total", 0))
        failed = int(s.get("failed", 0))
        errors = int(s.get("errors", 0))
        passed = int(s.get("passed", 0))
        skipped = int(s.get("skipped", 0))
        failing = failed + errors

        if failing == 0 and total > 0:
            status: RunStatus = STATUS_DONE
        elif passed == 0 and failing > 0:
            status = STATUS_FAILED
        else:
            status = STATUS_KNOWN_FAILURES if failing > 0 else STATUS_DONE

        started = s.get("started_at") or datetime.utcnow().isoformat()
        started_dt = datetime.fromisoformat(started.replace("Z", "+00:00")) if isinstance(started, str) else started

        commit_short = self.commit[:7] if self.commit else "local"
        title = f"#{self.run_number} · {self.branch} · {commit_short}"

        return TestRunRow(
            run_title=title,
            status=status,
            started_at=started_dt,
            duration_seconds=float(s.get("duration_seconds", 0.0)),
            total=total,
            passed=passed,
            failed=failing,
            skipped=skipped,
            branch=self.branch,
            commit=commit_short,
            ci_run_url=self.ci_run_url,
            allure_report_url=self.allure_report_url,
            triggered_by=self.triggered_by,
        )


__all__ = [
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_KNOWN_FAILURES",
    "STATUS_LEGACY_MAP",
    "NotionApiError",
    "NotionClient",
    "RunStatus",
    "TestRunRow",
    "TestRunRowBuilder",
]
