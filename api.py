"""Async API client for the neoom BEAAM local REST API."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import API_BASE, API_SITE_CONFIG, API_SITE_STATE, API_THING_STATES

_LOGGER = logging.getLogger(__name__)

# The BEAAM silently closes idle keep-alive sockets, so a reused connection can
# fail on the first write. One retry turns that into a non-event.
REQUEST_TIMEOUT = 10
REQUEST_ATTEMPTS = 3
RETRY_DELAY = 0.5


class BeaamApiError(Exception):
    """Generic BEAAM API error."""


class BeaamAuthError(BeaamApiError):
    """Authentication failed (HTTP 401/403)."""


class BeaamConnectionError(BeaamApiError):
    """Could not reach the BEAAM device."""


class BeaamApiClient:
    """Lightweight async wrapper around the neoom BEAAM local REST API."""

    def __init__(
        self,
        host: str,
        api_key: str,
        session: aiohttp.ClientSession,
    ) -> None:
        self._base = API_BASE.format(host=host)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._session = session

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str) -> Any:
        """GET with a retry, since the BEAAM drops idle keep-alive connections."""
        url = self._base + path
        last_err: Exception | None = None

        for attempt in range(REQUEST_ATTEMPTS):
            try:
                async with self._session.get(
                    url,
                    headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status == 401 or resp.status == 403:
                        raise BeaamAuthError(f"Authentication failed ({resp.status})")
                    resp.raise_for_status()
                    return await resp.json()
            except BeaamAuthError:
                raise
            except (
                aiohttp.ClientConnectionError,
                aiohttp.ClientPayloadError,
                asyncio.TimeoutError,
            ) as err:
                # Transient: stale socket, timeout or truncated response — retry.
                last_err = err
                if attempt + 1 < REQUEST_ATTEMPTS:
                    _LOGGER.debug(
                        "GET %s failed (attempt %d/%d): %s — retrying",
                        url, attempt + 1, REQUEST_ATTEMPTS, err,
                    )
                    await asyncio.sleep(RETRY_DELAY)
                    continue
            except Exception as err:
                raise BeaamApiError(f"Request to {url} failed: {err}") from err

        raise BeaamConnectionError(f"Cannot reach BEAAM at {url}: {last_err}") from last_err

    async def _post(self, path: str, payload: dict) -> None:
        url = self._base + path
        last_err: Exception | None = None

        for attempt in range(REQUEST_ATTEMPTS):
            try:
                async with self._session.post(
                    url,
                    headers=self._headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status == 401 or resp.status == 403:
                        raise BeaamAuthError(f"Authentication failed ({resp.status})")
                    if resp.status not in (200, 201, 202, 204):
                        resp.raise_for_status()
                    return
            except BeaamAuthError:
                raise
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as err:
                last_err = err
                if attempt + 1 < REQUEST_ATTEMPTS:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
            except Exception as err:
                raise BeaamApiError(f"POST to {url} failed: {err}") from err

        raise BeaamConnectionError(f"Cannot reach BEAAM at {url}: {last_err}") from last_err

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_site_state(self) -> dict:
        """Return the high-level site energy flow state."""
        return await self._get(API_SITE_STATE)

    async def get_site_configuration(self) -> dict:
        """Return the site configuration including all Things and their IDs."""
        return await self._get(API_SITE_CONFIG)

    async def get_thing_states(self, thing_id: str) -> dict:
        """Return all DataPoint states for a specific Thing."""
        path = API_THING_STATES.format(thing_id=thing_id)
        return await self._get(path)

    async def set_thing_states(self, thing_id: str, states: list[dict]) -> None:
        """Write states to a Thing (e.g. emergency reserve).

        states example:
            [{"key": "EMERGENCY_RESERVE", "value": 20}]
        """
        path = f"/things/{thing_id}/states"
        return await self._post(path, states)

    async def call_thing_command(self, thing_id: str, command: str, params: dict | None = None) -> None:
        """Call a command on a Thing (e.g. start/stop charging)."""
        path = f"/things/{thing_id}/commands"
        payload = {"command": command}
        if params:
            payload["params"] = params
        return await self._post(path, payload)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def extract_state_value(states: list[dict], key: str) -> Any:
        """Return the value of a named DataPoint from a states list, or None."""
        for item in states:
            if item.get("key") == key:
                return item.get("value")
        return None
