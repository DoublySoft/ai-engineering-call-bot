"""Minimal Retell REST client. Endpoints and fields verified 2026-10-27 against
docs.retellai.com: create-retell-llm, create-agent, v3/create-web-call, v2/get-call."""

from typing import Any

import httpx

from voice_lab.core.errors import LabError

BASE_URL = "https://api.retellai.com"


class RetellError(LabError):
    pass


class RetellClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None):
        self._http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=20.0)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._http.request(method, path, json=json, headers=self._headers)
        except httpx.HTTPError as exc:
            raise RetellError(f"Retell unreachable: {type(exc).__name__}", 502) from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("message") or response.text
            except ValueError:
                message = response.text
            raise RetellError(f"Retell {method} {path} failed ({response.status_code}): {str(message)[:300]}", 502)
        return response.json()

    async def create_retell_llm(self, *, general_prompt: str, begin_message: str | None, model: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/create-retell-llm",
            {
                "general_prompt": general_prompt,
                "begin_message": begin_message,
                "start_speaker": "agent" if begin_message else "user",
                "model_config": {"type": "cascading", "primary": {"model": model}},
            },
        )

    async def create_agent(
        self, *, llm_id: str, voice_id: str, name: str, language: str, data_storage: str, max_call_ms: int
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/create-agent",
            {
                "response_engine": {"type": "retell-llm", "llm_id": llm_id},
                "voice_id": voice_id,
                "agent_name": name,
                "language": language,
                "data_storage_setting": data_storage,
                "max_call_duration_ms": max(60_000, min(max_call_ms, 7_200_000)),
            },
        )

    async def create_web_call(self, *, agent_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v3/create-web-call", {"agent_id": agent_id, "metadata": metadata})

    async def get_call(self, call_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/get-call/{call_id}")
