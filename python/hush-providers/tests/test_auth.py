"""Tests for Keycloak authentication provider.

This module tests:
1. KeycloakTokenConfig - configuration validation
2. KeycloakTokenProvider - token fetching and caching
3. Background refresh - daemon thread behavior
4. ResourceHub integration - keycloak reference resolution in LLM configs
"""

import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from hush.providers.auth import (
    KeycloakTokenConfig,
    KeycloakTokenProvider,
    create_auth,
)
from hush.providers.registry import auth_plugin

# =============================================================================
# KeycloakTokenConfig Tests
# =============================================================================


class TestKeycloakTokenConfig:
    """Tests for KeycloakTokenConfig."""

    def test_config_creation(self):
        """Test basic config creation."""
        config = KeycloakTokenConfig(
            url="https://identity.example.com/connect",
            name="test_app",
            secret="test_secret",
        )
        assert config.url == "https://identity.example.com/connect"
        assert config.name == "test_app"
        assert config.secret == "test_secret"
        assert config.token_path == "accessToken"  # default
        assert config.refresh_interval == 3600.0  # default
        print("✓ Basic config creation")

    def test_config_with_custom_values(self):
        """Test config with custom values."""
        config = KeycloakTokenConfig(
            url="https://identity.example.com/connect",
            name="test_app",
            secret="test_secret",
            token_path="access_token",
            expires_in_path="expires_in",
            refresh_interval=1800.0,
            refresh_buffer=60.0,
        )
        assert config.token_path == "access_token"
        assert config.expires_in_path == "expires_in"
        assert config.refresh_interval == 1800.0
        assert config.refresh_buffer == 60.0
        print("✓ Config with custom values")

    def test_config_category(self):
        """Test that config has correct category."""
        assert KeycloakTokenConfig._category == "keycloak"
        print("✓ Config category is 'keycloak'")

    def test_config_from_dict(self):
        """Test config creation from dictionary (like YAML loading)."""
        data = {
            "url": "https://identity.example.com/connect",
            "name": "test_app",
            "secret": "test_secret",
            "refresh_interval": 7200,
        }
        config = KeycloakTokenConfig.model_validate(data)
        assert config.url == "https://identity.example.com/connect"
        assert config.refresh_interval == 7200.0
        print("✓ Config from dict")


# =============================================================================
# KeycloakTokenProvider Tests
# =============================================================================


class TestKeycloakTokenProvider:
    """Tests for KeycloakTokenProvider."""

    @pytest.fixture
    def config(self):
        """Create a test config."""
        return KeycloakTokenConfig(
            url="https://identity.example.com/connect",
            name="test_app",
            secret="test_secret",
            refresh_interval=3600.0,
            refresh_buffer=300.0,
        )

    @pytest.fixture
    def mock_response(self):
        """Create a mock HTTP response."""
        response = Mock()
        response.json.return_value = {
            "accessToken": "mock_token_12345",
            "expiresIn": 3600,
        }
        response.raise_for_status = Mock()
        return response

    def test_provider_creation(self, config):
        """Test provider creation."""
        provider = KeycloakTokenProvider(config)
        assert provider.config == config
        assert provider._token is None
        assert provider._expires_at == 0
        assert provider._started is False
        print("✓ Provider creation")

    @patch("hush.providers.auth.keycloak.httpx.Client")
    def test_get_token_fetches_on_first_call(self, mock_client_class, config, mock_response):
        """Test that get_token fetches token on first call."""
        # Setup mock
        mock_client = MagicMock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        provider = KeycloakTokenProvider(config)
        token = provider.get_token()

        assert token == "mock_token_12345"
        assert provider._token == "mock_token_12345"
        mock_client.post.assert_called_once()
        print("✓ Token fetched on first call")

    @patch("hush.providers.auth.keycloak.httpx.Client")
    def test_get_token_returns_cached(self, mock_client_class, config, mock_response):
        """Test that get_token returns cached token."""
        # Setup mock
        mock_client = MagicMock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        provider = KeycloakTokenProvider(config)

        # First call - fetches
        token1 = provider.get_token()
        # Second call - should use cache
        token2 = provider.get_token()

        assert token1 == token2
        # Should only call post once (cached on second call)
        assert mock_client.post.call_count == 1
        print("✓ Token returned from cache")

    @patch("hush.providers.auth.keycloak.httpx.Client")
    def test_invalidate_clears_cache(self, mock_client_class, config, mock_response):
        """Test that invalidate clears the cached token."""
        # Setup mock
        mock_client = MagicMock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        provider = KeycloakTokenProvider(config)

        # First call - fetches
        provider.get_token()
        assert provider._token is not None

        # Invalidate
        provider.invalidate()
        assert provider._token is None
        assert provider._expires_at == 0

        # Next call should fetch again
        provider.get_token()
        assert mock_client.post.call_count == 2
        print("✓ Invalidate clears cache")

    def test_background_thread_is_daemon(self, config):
        """Test that background thread is a daemon thread."""
        provider = KeycloakTokenProvider(config)

        # Manually set token to avoid actual fetch
        provider._token = "test_token"
        provider._expires_at = time.time() + 3600

        # Trigger thread start
        provider._ensure_started()

        assert provider._refresh_thread is not None
        assert provider._refresh_thread.daemon is True
        assert provider._started is True

        # Cleanup
        provider.shutdown()
        print("✓ Background thread is daemon")

    def test_shutdown_stops_background_thread(self, config):
        """Test that shutdown stops the background thread."""
        provider = KeycloakTokenProvider(config)

        # Manually set token to avoid actual fetch
        provider._token = "test_token"
        provider._expires_at = time.time() + 3600

        # Start background thread
        provider._ensure_started()
        assert provider._refresh_thread.is_alive()

        # Shutdown
        provider.shutdown()

        # Wait a bit for thread to stop
        time.sleep(0.1)
        assert not provider._refresh_thread.is_alive()
        assert provider._started is False
        print("✓ Shutdown stops background thread")

    def test_shutdown_all_clears_instances(self, config):
        """Test that shutdown_all clears all provider instances."""
        KeycloakTokenProvider(
            KeycloakTokenConfig(
                url="https://example.com/1",
                name="app1",
                secret="secret1",
            )
        )
        KeycloakTokenProvider(
            KeycloakTokenConfig(
                url="https://example.com/2",
                name="app2",
                secret="secret2",
            )
        )

        assert "app1" in KeycloakTokenProvider._instances
        assert "app2" in KeycloakTokenProvider._instances

        KeycloakTokenProvider.shutdown_all()

        assert len(KeycloakTokenProvider._instances) == 0
        print("✓ shutdown_all clears instances")


# =============================================================================
# AuthFactory Tests
# =============================================================================


class TestAuthFactory:
    """Tests for AuthFactory."""

    def test_factory_creates_provider(self):
        """Test that factory creates KeycloakTokenProvider."""
        config = KeycloakTokenConfig(
            url="https://identity.example.com/connect",
            name="test_app",
            secret="test_secret",
        )
        provider = create_auth(config)

        assert isinstance(provider, KeycloakTokenProvider)
        assert provider.config == config
        print("✓ Factory creates provider")


# =============================================================================
# AuthPlugin Tests
# =============================================================================


class TestAuthPlugin:
    """Tests for AuthPlugin."""

    def test_plugin_is_registered(self):
        """Test that plugin auto-registers on import."""
        assert auth_plugin._registered is True
        print("✓ Plugin is auto-registered")

    def test_plugin_register_idempotent(self):
        """Test that register() is idempotent."""
        auth_plugin.register()
        auth_plugin.register()
        auth_plugin.register()
        assert auth_plugin._registered is True
        print("✓ Plugin register is idempotent")


# =============================================================================
# ResourceHub Integration Tests
# =============================================================================


class TestResourceHubIntegration:
    """Tests for ResourceHub keycloak integration."""

    @pytest.fixture
    def temp_config_file(self, tmp_path):
        """Create a temporary config file."""
        config_content = """
keycloak:test:
  url: https://identity.example.com/connect
  name: test_app
  secret: test_secret
  token_path: accessToken
  refresh_interval: 3600

llm:test-llm:
  api_type: openai
  api_key: keycloak:test
  base_url: https://api.example.com/v1
  model: test-model

llm:static-llm:
  api_type: openai
  api_key: sk-static-key
  base_url: https://api.example.com/v1
  model: test-model
"""
        config_file = tmp_path / "resources.yaml"
        config_file.write_text(config_content)
        return config_file

    def test_keycloak_config_loads(self, temp_config_file):
        """Test that keycloak config loads from YAML."""
        from hush.core.registry import ResourceHub

        hub = ResourceHub.from_yaml(temp_config_file)
        config = hub.get_config("keycloak:test")

        assert isinstance(config, KeycloakTokenConfig)
        assert config.url == "https://identity.example.com/connect"
        assert config.name == "test_app"
        assert config.refresh_interval == 3600.0
        print("✓ Keycloak config loads from YAML")

    def test_keycloak_accessor(self, temp_config_file):
        """Test hub.keycloak() accessor."""
        from hush.core.registry import ResourceHub

        hub = ResourceHub.from_yaml(temp_config_file)

        # Mock the token fetch
        with patch("hush.providers.auth.keycloak.httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_response = Mock()
            mock_response.json.return_value = {"accessToken": "test_token", "expiresIn": 3600}
            mock_response.raise_for_status = Mock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            provider = hub.keycloak("test")
            assert isinstance(provider, KeycloakTokenProvider)

            token = provider.get_token()
            assert token == "test_token"
        print("✓ hub.keycloak() accessor works")

    def test_llm_with_keycloak_reference(self, temp_config_file):
        """Test that LLM with keycloak:xxx api_key resolves token."""
        from hush.core.registry import ResourceHub

        hub = ResourceHub.from_yaml(temp_config_file)

        # Check config has keycloak reference
        llm_config = hub.get_config("llm:test-llm")
        assert llm_config.api_key == "keycloak:test"
        print("✓ LLM config has keycloak reference")

    def test_llm_with_static_key(self, temp_config_file):
        """Test that LLM with static api_key works normally."""
        from hush.core.registry import ResourceHub

        hub = ResourceHub.from_yaml(temp_config_file)

        llm_config = hub.get_config("llm:static-llm")
        assert llm_config.api_key == "sk-static-key"
        print("✓ LLM with static key works")

    def test_resolve_api_key_static(self, temp_config_file):
        """Test _resolve_api_key with static key."""
        from hush.core.registry import ResourceHub

        hub = ResourceHub.from_yaml(temp_config_file)
        result = hub._resolve_api_key("sk-static-key")
        assert result == "sk-static-key"
        print("✓ _resolve_api_key returns static key unchanged")

    def test_resolve_api_key_keycloak(self, temp_config_file):
        """Test _resolve_api_key with keycloak reference."""
        from hush.core.registry import ResourceHub

        hub = ResourceHub.from_yaml(temp_config_file)

        with patch("hush.providers.auth.keycloak.httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_response = Mock()
            mock_response.json.return_value = {"accessToken": "resolved_token", "expiresIn": 3600}
            mock_response.raise_for_status = Mock()
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = hub._resolve_api_key("keycloak:test")
            assert result == "resolved_token"
        print("✓ _resolve_api_key resolves keycloak reference")


# =============================================================================
# Real Integration Tests (with actual Keycloak endpoint)
# =============================================================================


def _can_reach_keycloak():
    """Check if keycloak server is reachable."""
    import socket

    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("identity.example.com", 443))
        return True
    except (socket.timeout, socket.error, OSError):
        return False


@pytest.mark.integration
@pytest.mark.skipif(
    not _can_reach_keycloak(), reason="Cannot reach keycloak server (keycloak server not reachable)"
)
class TestRealKeycloakIntegration:
    """Integration tests using real keycloak:myapp config from resources.yaml.

    These tests require:
    - .env file with HUSH_CONFIG pointing to resources.yaml
    - Valid keycloak credentials in .env
    - Network access to keycloak server
    """

    @pytest.fixture
    def real_hub(self):
        """Get real ResourceHub from HUSH_CONFIG."""
        from dotenv import load_dotenv

        load_dotenv()

        from hush.core.registry import get_hub

        import hush.providers  # noqa: F401 - Register plugins

        return get_hub()

    def test_keycloak_config_loads_from_real_yaml(self, real_hub):
        """Test keycloak:myapp config loads from real resources.yaml."""
        config = real_hub.get_config("keycloak:myapp")

        assert isinstance(config, KeycloakTokenConfig)
        assert "identity" in config.url  # identity server
        assert config.token_path == "accessToken"
        assert config.refresh_interval > 0
        print(f"✓ Keycloak config loaded: url={config.url}")
        print(f"  refresh_interval={config.refresh_interval}s")

    def test_keycloak_provider_fetches_real_token(self, real_hub):
        """Test that keycloak provider can fetch a real token."""
        provider = real_hub.keycloak("myapp")

        assert isinstance(provider, KeycloakTokenProvider)

        # Fetch real token
        token = provider.get_token()

        assert token is not None
        assert len(token) > 0
        assert provider._expires_at > time.time()  # Token not expired

        print(f"✓ Real token fetched: {token[:20]}...")
        print(f"  expires_at: {provider._expires_at}")

        # Cleanup
        provider.shutdown()

    def test_keycloak_token_is_cached(self, real_hub):
        """Test that subsequent calls return cached token."""
        provider = real_hub.keycloak("myapp")

        # First call - fetches from server
        token1 = provider.get_token()
        expires_at_1 = provider._expires_at

        # Second call - should return cached
        token2 = provider.get_token()
        expires_at_2 = provider._expires_at

        assert token1 == token2
        assert expires_at_1 == expires_at_2
        print("✓ Token is cached correctly")

        provider.shutdown()

    def test_keycloak_invalidate_forces_refetch(self, real_hub):
        """Test that invalidate() forces a new token fetch."""
        provider = real_hub.keycloak("myapp")

        # First fetch
        provider.get_token()

        # Invalidate
        provider.invalidate()
        assert provider._token is None

        # Second fetch - should get new token
        token2 = provider.get_token()
        assert token2 is not None

        # Note: tokens might be the same if fetched quickly, but _token should be set
        assert provider._token is not None
        print("✓ Invalidate forces refetch")

        provider.shutdown()

    def test_llm_config_with_keycloak_reference(self, real_hub):
        """Test that LLM config with keycloak reference is loaded correctly."""
        llm_config = real_hub.get_config("llm:claude-4-sonnet")

        assert llm_config.api_key == "keycloak:myapp"
        assert "myapp" in llm_config.base_url
        print("✓ LLM config loaded with keycloak reference")
        print(f"  api_key: {llm_config.api_key}")
        print(f"  base_url: {llm_config.base_url}")

    def test_resolve_api_key_with_real_keycloak(self, real_hub):
        """Test _resolve_api_key with real keycloak reference."""
        resolved_token = real_hub._resolve_api_key("keycloak:myapp")

        assert resolved_token is not None
        assert len(resolved_token) > 0
        assert not resolved_token.startswith("keycloak:")  # Should be actual token
        print(f"✓ API key resolved: {resolved_token[:20]}...")

    @pytest.mark.asyncio
    async def test_llm_with_keycloak_auth(self, real_hub):
        """Test full LLM flow with keycloak authentication.

        This test:
        1. Gets LLM via hub.llm("claude-4-sonnet")
        2. Verifies keycloak token is resolved
        3. Makes an actual LLM generate request
        """
        from hush.providers.llms.base import BaseLLM

        # Get LLM - should resolve keycloak token automatically
        llm = real_hub.llm("claude-4-sonnet")

        assert isinstance(llm, BaseLLM)
        assert hasattr(llm, "_keycloak_provider")  # Should have keycloak reference
        assert hasattr(llm, "_original_config")
        print("✓ LLM created with keycloak auth")
        print(f"  model: {llm.config.model}")
        print(f"  base_url: {llm.config.base_url}")

        # Verify the api_key is resolved (not the keycloak: reference)
        assert not llm.config.api_key.startswith("keycloak:")
        print(f"  api_key resolved: {llm.config.api_key[:20]}...")

        # Make actual LLM request (async)
        response = await llm.generate(
            messages=[{"role": "user", "content": "Say 'hello' in one word."}],
            temperature=0,
            max_tokens=10,
        )

        assert response is not None
        assert response.choices is not None
        assert len(response.choices) > 0
        content = response.choices[0].message.content
        assert content is not None
        print("✓ LLM generate successful")
        print(f"  response: {content}")

    def test_cached_llm_token_refresh(self, real_hub):
        """Test that cached LLM gets fresh token on subsequent calls.

        This test verifies:
        1. LLM is created and cached with initial token
        2. When token is invalidated, next hub.llm() call refreshes it
        3. The client's api_key is updated with fresh token
        """
        # First call - creates and caches LLM
        llm1 = real_hub.llm("claude-4-sonnet")
        initial_token = llm1.client.api_key
        print("✓ Initial LLM created")
        print(f"  token: {initial_token[:20]}...")

        # Verify LLM has keycloak provider
        assert hasattr(llm1, "_keycloak_provider")
        provider = llm1._keycloak_provider

        # Invalidate the token (simulates expiry)
        provider.invalidate()
        assert provider._token is None
        print("✓ Token invalidated")

        # Second call - should return cached LLM with refreshed token
        llm2 = real_hub.llm("claude-4-sonnet")

        # Should be the same instance (cached)
        assert llm1 is llm2
        print("✓ Same cached instance returned")

        # Token should be refreshed
        refreshed_token = llm2.client.api_key
        assert refreshed_token is not None
        assert len(refreshed_token) > 0
        # Token might be same if fetched quickly, but should be valid
        assert not refreshed_token.startswith("keycloak:")
        print("✓ Token refreshed on cached LLM")
        print(f"  token: {refreshed_token[:20]}...")

        # Config should also be updated
        assert llm2.config.api_key == refreshed_token
        print("✓ Config api_key also updated")

    def test_background_thread_starts(self, real_hub):
        """Test that background refresh thread starts correctly."""
        provider = real_hub.keycloak("myapp")

        # Trigger background thread by getting token
        provider.get_token()

        assert provider._started is True
        assert provider._refresh_thread is not None
        assert provider._refresh_thread.daemon is True
        assert provider._refresh_thread.is_alive()
        print("✓ Background thread started as daemon")

        provider.shutdown()

    def test_background_thread_stops_on_shutdown(self, real_hub):
        """Test that background thread stops cleanly on shutdown."""
        provider = real_hub.keycloak("myapp")
        provider.get_token()

        assert provider._refresh_thread.is_alive()

        provider.shutdown()
        time.sleep(0.1)

        assert not provider._refresh_thread.is_alive()
        print("✓ Background thread stopped cleanly")


# =============================================================================
# Background Refresh Tests (Unit)
# =============================================================================


class TestBackgroundRefresh:
    """Unit tests for background token refresh behavior."""

    def test_background_error_does_not_crash(self):
        """Test that background refresh errors don't crash the app."""
        config = KeycloakTokenConfig(
            url="https://invalid.example.com/connect",
            name="error_test",
            secret="test_secret",
            refresh_interval=0.1,
        )

        provider = KeycloakTokenProvider(config)

        # Manually set valid token to start background thread
        provider._token = "manual_token"
        provider._expires_at = time.time() + 3600
        provider._ensure_started()

        # Wait for background to attempt refresh (will fail)
        time.sleep(0.2)

        # App should not crash, thread should still be running or stopped gracefully
        # The manual token should still be accessible
        with provider._lock:
            assert provider._token == "manual_token"

        provider.shutdown()
        print("✓ Background error does not crash")


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Tests for thread safety of KeycloakTokenProvider."""

    @patch("hush.providers.auth.keycloak.httpx.Client")
    def test_concurrent_get_token_calls(self, mock_client_class):
        """Test that concurrent get_token calls are thread-safe."""

        # Setup mock with delay to simulate network latency
        def slow_post(*args, **kwargs):
            time.sleep(0.05)  # 50ms delay
            response = Mock()
            response.json.return_value = {"accessToken": "concurrent_token", "expiresIn": 3600}
            response.raise_for_status = Mock()
            return response

        mock_client = MagicMock()
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client.post.side_effect = slow_post
        mock_client_class.return_value = mock_client

        config = KeycloakTokenConfig(
            url="https://identity.example.com/connect",
            name="concurrent_test",
            secret="test_secret",
        )

        provider = KeycloakTokenProvider(config)
        results = []
        errors = []

        def get_token():
            try:
                token = provider.get_token()
                results.append(token)
            except Exception as e:
                errors.append(e)

        # Create multiple threads
        threads = [threading.Thread(target=get_token) for _ in range(10)]

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all to complete
        for t in threads:
            t.join()

        # All should succeed with same token
        assert len(errors) == 0
        assert len(results) == 10
        assert all(token == "concurrent_token" for token in results)

        provider.shutdown()
        print("✓ Concurrent get_token calls are thread-safe")


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print(" Testing hush-providers auth module ".center(60, "="))
    print("=" * 60 + "\n")

    # Run config tests
    print("\n--- KeycloakTokenConfig Tests ---")
    config_tests = TestKeycloakTokenConfig()
    config_tests.test_config_creation()
    config_tests.test_config_with_custom_values()
    config_tests.test_config_category()
    config_tests.test_config_from_dict()

    # Run factory tests
    print("\n--- AuthFactory Tests ---")
    factory_tests = TestAuthFactory()
    factory_tests.test_factory_creates_provider()

    # Run plugin tests
    print("\n--- AuthPlugin Tests ---")
    plugin_tests = TestAuthPlugin()
    plugin_tests.test_plugin_is_registered()
    plugin_tests.test_plugin_register_idempotent()

    print("\n" + "=" * 60)
    print(" Basic tests passed! ".center(60, "="))
    print(" Run pytest for full test suite ".center(60))
    print("=" * 60)
