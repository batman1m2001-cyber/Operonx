"""OAuth2 ``client_credentials`` token provider with background refresh.

The same shape as :mod:`operonx.providers.auth.keycloak` — lazy first
fetch, a daemon thread refreshing ahead of expiry, a lock around the
cached token — for endpoints that speak plain OAuth2 instead of
Keycloak's response format.

Both providers expose ``get_token()``, which is the whole interface
:class:`~operonx.core.registry.ResourceHub` needs to resolve an
``api_key: oauth2:<name>`` reference.
"""

import atexit
import threading
import time
from typing import Dict, Optional

import httpx

from operonx.core.loggings import LOGGER

from .config import OAuth2TokenConfig


class OAuth2TokenProvider:
    """Thread-safe OAuth2 client_credentials token provider.

    Example::

        config = OAuth2TokenConfig(
            token_url="https://workspace.cloud.databricks.com/oidc/v1/token",
            client_id="my-client-id",
            client_secret="my-client-secret",
            scope="all-apis",
        )
        provider = OAuth2TokenProvider(config)
        token = provider.get_token()
        provider.shutdown()
    """

    _instances: Dict[str, "OAuth2TokenProvider"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, config: OAuth2TokenConfig):
        self.config = config
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None
        self._started = False

        with self._instances_lock:
            self._instances[config.client_id] = self

    # ------------------------------------------------------------------
    # Token fetch
    # ------------------------------------------------------------------

    def _fetch_token(self) -> str:
        """Fetch a new token. Caller must hold ``self._lock``.

        Raises:
            httpx.HTTPStatusError: the endpoint rejected the request.
            ValueError: the response carried no token at ``token_path``.
        """
        try:
            with httpx.Client(verify=self.config.verify_ssl, timeout=30.0) as client:
                response = client.post(
                    self.config.token_url,
                    data={
                        "grant_type": "client_credentials",
                        "scope": self.config.scope,
                    },
                    auth=(self.config.client_id, self.config.client_secret),
                )
                response.raise_for_status()
                data = response.json()

            token = data.get(self.config.token_path)
            if not token:
                raise ValueError(f"Token not found at '{self.config.token_path}' in response")

            expires_in = None
            if self.config.expires_in_path:
                expires_in = data.get(self.config.expires_in_path)

            if expires_in:
                self._expires_at = time.time() + expires_in - self.config.refresh_buffer
            else:
                # No lifetime advertised — assume 59 minutes, the common
                # OAuth2 default, minus nothing (the buffer is already
                # baked into the number).
                self._expires_at = time.time() + 3540

            self._token = token
            LOGGER.info(
                "[OAuth2TokenProvider:%s] Token fetched, expires in %ds",
                self.config.client_id,
                expires_in or 3600,
            )
            return token

        except httpx.HTTPStatusError as e:
            LOGGER.error(
                "[OAuth2TokenProvider:%s] HTTP error: %d - %s",
                self.config.client_id,
                e.response.status_code,
                e.response.text,
            )
            raise
        except Exception as e:
            LOGGER.error(
                "[OAuth2TokenProvider:%s] Error fetching token: %s",
                self.config.client_id,
                e,
            )
            raise

    # ------------------------------------------------------------------
    # Background refresh
    # ------------------------------------------------------------------

    def _background_refresh_loop(self) -> None:
        LOGGER.info(
            "[OAuth2TokenProvider:%s] Background refresh started (interval=%ds)",
            self.config.client_id,
            self.config.refresh_interval,
        )

        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(timeout=self.config.refresh_interval)
            if self._shutdown_event.is_set():
                break

            with self._lock:
                needs_refresh = not self._token or time.time() >= (
                    self._expires_at - self.config.refresh_buffer
                )

            if needs_refresh:
                try:
                    with self._lock:
                        self._fetch_token()
                    LOGGER.debug(
                        "[OAuth2TokenProvider:%s] Background refresh successful",
                        self.config.client_id,
                    )
                except Exception as e:
                    # Never raised out of the daemon: the next get_token()
                    # retries synchronously, and a dead refresh thread
                    # would be worse than a slow call.
                    LOGGER.warning(
                        "[OAuth2TokenProvider:%s] Background refresh failed: %s",
                        self.config.client_id,
                        e,
                    )

        LOGGER.info(
            "[OAuth2TokenProvider:%s] Background refresh stopped",
            self.config.client_id,
        )

    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._shutdown_event.clear()
            self._refresh_thread = threading.Thread(
                target=self._background_refresh_loop,
                name=f"OAuth2Refresh-{self.config.client_id}",
                daemon=True,
            )
            self._refresh_thread.start()
            self._started = True

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def get_token(self) -> str:
        """Return a valid access token, fetching or refreshing as needed."""
        self._ensure_started()
        with self._lock:
            if self._token and time.time() < self._expires_at:
                return self._token
            return self._fetch_token()

    def invalidate(self) -> None:
        """Force a fresh fetch on the next :meth:`get_token`."""
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def shutdown(self) -> None:
        """Stop the background refresh thread."""
        self._shutdown_event.set()
        if self._refresh_thread and self._refresh_thread.is_alive():
            self._refresh_thread.join(timeout=2.0)
        self._started = False

    @classmethod
    def shutdown_all(cls) -> None:
        """Shut down every provider built in this process."""
        with cls._instances_lock:
            for provider in cls._instances.values():
                provider.shutdown()
            cls._instances.clear()


atexit.register(OAuth2TokenProvider.shutdown_all)
