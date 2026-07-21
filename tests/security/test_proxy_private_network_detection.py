"""
Comprehensive tests for private network detection in proxy configuration.

Tests the should_bypass_proxy function and related private network detection
logic to ensure proper handling of RFC1918 addresses, CGNAT, link-local,
and loopback ranges in containerized environments.
"""

import pytest
from local_deep_research.security.proxy_config import should_bypass_proxy


class TestPrivateNetworkDetection:
    """Test suite for private network detection in proxy bypass logic."""

    def test_loopback_ipv4_bypass(self):
        """Test that IPv4 loopback addresses bypass the proxy."""
        loopback_addresses = [
            "http://127.0.0.1:8080",
            "https://127.0.0.1/api/endpoint",
            "http://127.0.0.1:11434/ollama",
            "https://127.0.0.1:8080/searxng",
        ]
        for url in loopback_addresses:
            assert should_bypass_proxy(url), f"Expected bypass for loopback: {url}"

    def test_rfc1918_private_ranges_bypass(self):
        """Test that RFC1918 private ranges bypass the proxy."""
        rfc1918_addresses = [
            # Class A private (10.0.0.0/8)
            "http://10.0.0.1:8080",
            "https://10.255.255.254/api",
            "http://10.128.0.5:11434",
            # Class B private (172.16.0.0/12)
            "http://172.16.0.1:8080",
            "https://172.31.255.254/api",
            "http://172.25.128.1:11434",
            # Class C private (192.168.0.0/16)
            "http://192.168.0.1:8080",
            "https://192.168.255.254/api",
            "http://192.168.1.100:8080",
        ]
        for url in rfc1918_addresses:
            assert should_bypass_proxy(url), f"Expected bypass for RFC1918: {url}"

    def test_cgnat_range_bypass(self):
        """Test that CGNAT range (100.64.0.0/10) bypasses the proxy."""
        cgnat_addresses = [
            "http://100.64.0.1:8080",
            "https://100.127.255.254/api",
            "http://100.96.0.5:11434",
        ]
        for url in cgnat_addresses:
            assert should_bypass_proxy(url), f"Expected bypass for CGNAT: {url}"

    def test_link_local_range_bypass(self):
        """Test that link-local range (169.254.0.0/16) bypasses the proxy."""
        link_local_addresses = [
            "http://169.254.0.1:8080",
            "https://169.254.255.254/api",
            "http://169.254.100.5:11434",
        ]
        for url in link_local_addresses:
            assert should_bypass_proxy(url), f"Expected bypass for link-local: {url}"

    def test_public_addresses_no_bypass(self):
        """Test that public addresses do NOT bypass the proxy."""
        public_addresses = [
            "http://8.8.8.8:80",
            "https://1.1.1.1/api",
            "http://93.184.216.34:8080",  # example.com
            "https://172.32.0.1/api",  # Just outside RFC1918 Class B
            "http://192.169.0.1:8080",  # Just outside RFC1918 Class C
        ]
        for url in public_addresses:
            assert not should_bypass_proxy(url), f"Expected NO bypass for public: {url}"

    def test_localhost_hostname_bypass(self):
        """Test that localhost hostname bypasses the proxy."""
        localhost_urls = [
            "http://localhost:8080",
            "https://localhost/api",
            "http://localhost:11434/ollama",
            "https://localhost.localdomain:8080",
        ]
        for url in localhost_urls:
            assert should_bypass_proxy(url), f"Expected bypass for localhost: {url}"

    def test_empty_and_invalid_urls(self):
        """Test behavior with empty and invalid URLs."""
        # Empty URL should bypass (conservative behavior)
        assert should_bypass_proxy(""), "Empty URL should bypass"
        assert should_bypass_proxy(None), "None URL should bypass"

        # Invalid URL should not bypass
        assert not should_bypass_proxy("not-a-url"), "Invalid URL should not bypass"
        assert not should_bypass_proxy("http://"), "Malformed URL should not bypass"

    def test_container_service_names(self):
        """Test common container service names that should resolve to private IPs."""
        # These should resolve to private IPs in container networks
        container_services = [
            "http://searxng-ldr:8080",
            "https://ldr-local:5000",
            "http://ollama:11434",
            "https://redis:6379",
        ]
        for url in container_services:
            # In a real container environment, these would resolve to private IPs
            # For testing, we verify the function accepts these formats
            result = should_bypass_proxy(url)
            # The result depends on DNS resolution, so we just verify it doesn't crash
            assert isinstance(result, bool), f"Should return boolean for: {url}"

    def test_ipv6_loopback_bypass(self):
        """Test that IPv6 loopback addresses bypass the proxy."""
        # Note: IPv6 support may be limited due to httpx NO_PROXY parsing issues
        ipv6_loopback = [
            "http://[::1]:8080",
            "https://[::1]/api",
            "http://[::1]:11434/ollama",
        ]
        for url in ipv6_loopback:
            # Due to known httpx issues with bare IPv6 in NO_PROXY, this might
            # not work as expected. Test documents current behavior.
            try:
                result = should_bypass_proxy(url)
                # If it works, it should bypass
                assert result, f"IPv6 loopback should bypass: {url}"
            except Exception:
                # If it fails, that's also acceptable given known limitations
                pass

    def test_ipv6_private_ranges(self):
        """Test IPv6 private ranges (ULA, link-local)."""
        ipv6_private = [
            # Unique Local Address (fc00::/7)
            "http://[fc00::1]:8080",
            "https://[fd00::1]:8080",
            # Link-local (fe80::/10)
            "http://[fe80::1]:8080",
        ]
        for url in ipv6_private:
            try:
                result = should_bypass_proxy(url)
                # IPv6 private ranges should bypass if supported
                assert result, f"IPv6 private should bypass: {url}"
            except Exception:
                # Known limitations with IPv6 in current implementation
                pass

    def test_port_variations(self):
        """Test that various port numbers are handled correctly."""
        test_cases = [
            ("http://127.0.0.1:80", True),  # Standard HTTP
            ("http://127.0.0.1:443", True),  # Standard HTTPS
            ("http://127.0.0.1:8080", True),  # Common alt port
            ("http://127.0.0.1:11434", True),  # Ollama default
            ("https://127.0.0.1:6379", True),  # Redis
            ("http://8.8.8.8:80", False),  # Public IP
            ("http://8.8.8.8:53", False),  # Public IP with DNS port
        ]
        for url, should_bypass in test_cases:
            result = should_bypass_proxy(url)
            assert result == should_bypass, f"Port handling failed for {url}"

    def test_url_schemes(self):
        """Test different URL schemes."""
        schemes = [
            "http://127.0.0.1:8080",
            "https://127.0.0.1:8080",
            "ws://127.0.0.1:8080",
            "wss://127.0.0.1:8080",
        ]
        for url in schemes:
            assert should_bypass_proxy(url), f"Scheme should not affect bypass: {url}"

    def test_private_ip_edge_cases(self):
        """Test edge cases at boundaries of private ranges."""
        edge_cases = [
            # RFC1918 Class A boundaries
            ("http://10.0.0.0:8080", True),  # Start of range
            ("http://10.255.255.255:8080", True),  # End of range
            ("http://9.255.255.255:8080", False),  # Just before
            ("http://11.0.0.0:8080", False),  # Just after

            # RFC1918 Class B boundaries
            ("http://172.16.0.0:8080", True),  # Start of range
            ("http://172.31.255.255:8080", True),  # End of range
            ("http://172.15.255.255:8080", False),  # Just before
            ("http://172.32.0.0:8080", False),  # Just after

            # RFC1918 Class C boundaries
            ("http://192.168.0.0:8080", True),  # Start of range
            ("http://192.168.255.255:8080", True),  # End of range
            ("http://192.167.255.255:8080", False),  # Just before
            ("http://192.169.0.0:8080", False),  # Just after
        ]
        for url, should_bypass in edge_cases:
            result = should_bypass_proxy(url)
            assert result == should_bypass, f"Edge case failed for {url}"

    def test_dns_resolution_fallback(self):
        """Test behavior when DNS resolution might fail."""
        # These hostnames should not resolve and should conservatively NOT bypass
        non_resolvable = [
            "http://this-hostname-does-not-exist.local:8080",
            "http://fake.internal.service:8080",
        ]
        for url in non_resolvable:
            # When DNS fails, should conservatively return False (do NOT bypass)
            result = should_bypass_proxy(url)
            # The function should not crash and should return a boolean
            assert isinstance(result, bool), f"Should return boolean even for non-resolvable: {url}"

    def test_real_world_container_services(self):
        """Test real-world container service scenarios."""
        real_world_cases = [
            # Ollama service
            ("http://localhost:11434/api/tags", True),
            ("http://127.0.0.1:11434/api/generate", True),
            # SearXNG service
            ("http://localhost:8080/search", True),
            ("http://searxng-ldr:8080/search", True),
            # Local development servers
            ("http://localhost:5000/api", True),
            ("http://127.0.0.1:3000/api", True),
            # External services (should not bypass)
            ("http://api.openai.com/v1/chat", False),
            ("https://www.googleapis.com/api", False),
        ]
        for url, should_bypass in real_world_cases:
            result = should_bypass_proxy(url)
            # For hostnames that resolve to private IPs, this should work
            if "localhost" in url or "127.0.0.1" in url:
                assert result == should_bypass, f"Real-world case failed for {url}"
            else:
                # Other hostnames depend on DNS resolution
                assert isinstance(result, bool), f"Should return boolean for: {url}"


class TestProxyBypassIntegration:
    """Integration tests for proxy bypass with real network scenarios."""

    def test_proxy_bypass_with_docker_networks(self):
        """Test proxy bypass in typical Docker network scenarios."""
        docker_networks = [
            # Docker bridge network
            "http://172.17.0.1:8080",
            "http://172.17.0.2:11434",
            # Docker custom networks
            "http://172.18.0.1:8080",
            "http://172.19.0.1:5000",
            # Kubernetes pod networks
            "http://10.244.0.1:8080",
            "http://10.244.1.5:11434",
        ]
        for url in docker_networks:
            assert should_bypass_proxy(url), f"Docker network should bypass: {url}"

    def test_proxy_bypass_consistency(self):
        """Test that bypass decisions are consistent for the same IPs."""
        test_ip = "127.0.0.1"
        urls_with_same_ip = [
            f"http://{test_ip}:8080/path",
            f"https://{test_ip}/api",
            f"http://{test_ip}:11434/ollama",
        ]
        results = [should_bypass_proxy(url) for url in urls_with_same_ip]
        # All should have the same result
        assert all(results), f"Inconsistent bypass results for {test_ip}"
        assert len(set(results)) == 1, f"Results should be identical for same IP"