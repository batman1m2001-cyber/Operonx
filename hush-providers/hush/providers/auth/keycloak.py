"""Keycloak token provider with background refresh.

This module provides a thread-safe token provider that fetches OAuth tokens
from Keycloak endpoints with automatic background refresh.
"""

import atexit
import threading
import time
from typing import Optional

import httpx

from hush.core.loggings import LOGGER

from .config import KeycloakTokenConfig


class KeycloakTokenProvider:
    """Thread-safe Keycloak token provider with background refresh.

    Features:
    - Lazy token fetching on first access
    - Background daemon thread for proactive refresh
    - Thread-safe access via threading.Lock
    - Graceful shutdown via threading.Event
    - Error isolation (background errors logged, not raised)

    The background refresh runs as a daemon thread that:
    - Wakes up at refresh_interval or when token is near expiry
    - Does NOT interfere with main flow (errors are logged only)
    - Automatically stops when main program exits

    Example:
        config = KeycloakTokenConfig(
            url="https://identity.example.com/client/connect",
            name="my_app",
            secret="my_secret",
            token_path="accessToken"
        )
        provider = KeycloakTokenProvider(config)

        # Get token (fetches if needed, always returns valid token)
        token = provider.get_token()

        # Shutdown background refresh (optional, auto on exit)
        provider.shutdown()
    """

    # Track all providers for cleanup
    _instances: dict[str, "KeycloakTokenProvider"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, config: KeycloakTokenConfig):
        """Initialize the token provider.

        Args:
            config: KeycloakTokenConfig with endpoint and credentials
        """
        self.config = config
        self._token: Optional[str] = None
        self._expires_at: float = 0
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._refresh_thread: Optional[threading.Thread] = None
        self._started = False

        # Register for cleanup
        with self._instances_lock:
            self._instances[config.name] = self

    def _fetch_token(self) -> str:
        """Fetch a new token from Keycloak endpoint.

        Returns:
            Access token string

        Raises:
            httpx.HTTPStatusError: If HTTP request fails
            ValueError: If token not found in response
        """
        try:
            with httpx.Client(verify=False, timeout=30.0) as client:
                response = client.post(
                    self.config.url,
                    json={"Name": self.config.name, "Secret": self.config.secret},
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()

                # Extract token
                token = data.get(self.config.token_path)
                if not token:
                    raise ValueError(f"Token not found at '{self.config.token_path}' in response")

                # Extract expiry
                expires_in = None
                if self.config.expires_in_path:
                    expires_in = data.get(self.config.expires_in_path)

                # Set expiry with buffer
                if expires_in:
                    self._expires_at = time.time() + expires_in - self.config.refresh_buffer
                else:
                    # Default 59 minutes if not provided
                    self._expires_at = time.time() + 3540

                self._token = token
                LOGGER.info(
                    "[KeycloakTokenProvider:%s] Token fetched, expires in %ds",
                    self.config.name,
                    expires_in or 3600,
                )
                return token

        except httpx.HTTPStatusError as e:
            LOGGER.error(
                "[KeycloakTokenProvider:%s] HTTP error: %d - %s",
                self.config.name,
                e.response.status_code,
                e.response.text,
            )
            raise
        except Exception as e:
            LOGGER.error(
                "[KeycloakTokenProvider:%s] Error fetching token: %s",
                self.config.name,
                e,
            )
            raise

    def _background_refresh_loop(self):
        """Background thread loop for proactive token refresh.

        This loop:
        1. Waits for refresh_interval or until shutdown
        2. Checks if token needs refresh (near expiry)
        3. Fetches new token if needed
        4. Logs errors but does NOT crash
        """
        LOGGER.info(
            "[KeycloakTokenProvider:%s] Background refresh started (interval=%ds)",
            self.config.name,
            self.config.refresh_interval,
        )

        while not self._shutdown_event.is_set():
            # Wait for next refresh interval or shutdown
            self._shutdown_event.wait(timeout=self.config.refresh_interval)

            if self._shutdown_event.is_set():
                break

            # Check if refresh is needed
            with self._lock:
                needs_refresh = not self._token or time.time() >= (
                    self._expires_at - self.config.refresh_buffer
                )

            if needs_refresh:
                try:
                    with self._lock:
                        self._fetch_token()
                    LOGGER.debug(
                        "[KeycloakTokenProvider:%s] Background refresh successful",
                        self.config.name,
                    )
                except Exception as e:
                    # Log but don't crash - on-demand fetch is fallback
                    LOGGER.warning(
                        "[KeycloakTokenProvider:%s] Background refresh failed: %s",
                        self.config.name,
                        e,
                    )

        LOGGER.info(
            "[KeycloakTokenProvider:%s] Background refresh stopped",
            self.config.name,
        )

    def _ensure_started(self):
        """Start background refresh thread if not already started."""
        if self._started:
            return

        with self._lock:
            if self._started:
                return

            # Clear shutdown event in case of restart after shutdown
            self._shutdown_event.clear()

            self._refresh_thread = threading.Thread(
                target=self._background_refresh_loop,
                name=f"KeycloakRefresh-{self.config.name}",
                daemon=True,  # Won't block program exit
            )
            self._refresh_thread.start()
            self._started = True

    def get_token(self) -> str:
        """Get a valid access token.

        Thread-safe. Fetches new token if:
        - No token cached
        - Token has expired

        Also starts background refresh thread on first call.

        Returns:
            Valid access token string

        Raises:
            Exception: If token fetch fails
        """
        # Start background refresh (idempotent)
        self._ensure_started()

        with self._lock:
            # Return cached token if valid
            if self._token and time.time() < self._expires_at:
                return self._token

            # Fetch new token
            return self._fetch_token()

    def invalidate(self):
        """Force token refresh on next get_token() call."""
        with self._lock:
            self._token = None
            self._expires_at = 0

    def shutdown(self):
        """Stop background refresh thread.

        Called automatically on program exit via atexit.
        """
        self._shutdown_event.set()
        if self._refresh_thread and self._refresh_thread.is_alive():
            self._refresh_thread.join(timeout=2.0)
        self._started = False

    @classmethod
    def shutdown_all(cls):
        """Shutdown all registered providers."""
        with cls._instances_lock:
            for provider in cls._instances.values():
                provider.shutdown()
            cls._instances.clear()


# Register cleanup on program exit
atexit.register(KeycloakTokenProvider.shutdown_all)
