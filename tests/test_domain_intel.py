"""Tests for GA + Pixel extraction regex patterns."""

from backend.worker.domain_intel import extract_tracking_codes


class TestExtractTrackingCodes:
    def test_extract_ga4_from_gtag_config(self):
        html = """<script>gtag("config", "G-BM7X92K1")</script>"""
        codes = extract_tracking_codes(html, "https://example.com")
        assert any(c["type"] == "google_analytics" and c["id"] == "G-BM7X92K1" for c in codes)

    def test_extract_ga4_single_quotes(self):
        html = """<script>gtag('config', 'G-ABC12345')</script>"""
        codes = extract_tracking_codes(html, "https://example.com")
        assert any(c["type"] == "google_analytics" and c["id"] == "G-ABC12345" for c in codes)

    def test_extract_ua_from_legacy_analytics(self):
        html = """<script>ga("create", "UA-12345678-1", "auto")</script>"""
        codes = extract_tracking_codes(html, "https://example.com")
        ga_codes = [c for c in codes if c["type"] == "google_analytics"]
        assert any("UA-12345678-1" in c["id"] for c in ga_codes)

    def test_extract_facebook_pixel(self):
        html = """<script>fbq('init', '291847362');</script>"""
        codes = extract_tracking_codes(html, "https://example.com")
        assert any(c["type"] == "facebook_pixel" and c["id"] == "291847362" for c in codes)

    def test_extract_facebook_pixel_double_quotes(self):
        html = '''<script>fbq("init", "123456789012");</script>'''
        codes = extract_tracking_codes(html, "https://example.com")
        assert any(c["type"] == "facebook_pixel" and c["id"] == "123456789012" for c in codes)

    def test_no_tracking_codes(self):
        html = "<html><body>Hello world</body></html>"
        codes = extract_tracking_codes(html, "https://example.com")
        assert codes == []

    def test_multiple_codes_same_page(self):
        html = """
        <script>gtag("config", "G-ABC123")</script>
        <script>fbq('init', '999888777');</script>
        """
        codes = extract_tracking_codes(html, "https://example.com")
        types = {c["type"] for c in codes}
        assert "google_analytics" in types
        assert "facebook_pixel" in types

    def test_gtm_ignored(self):
        html = '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>'
        codes = extract_tracking_codes(html, "https://example.com")
        assert all(c["type"] != "gtm" for c in codes)

    def test_deduplicates_same_code(self):
        html = """
        <script>gtag("config", "G-SAME123")</script>
        <script>gtag("config", "G-SAME123")</script>
        """
        codes = extract_tracking_codes(html, "https://example.com")
        ga_codes = [c for c in codes if c["id"] == "G-SAME123"]
        assert len(ga_codes) == 1

    def test_snippet_is_truncated(self):
        html = '<script>gtag("config", "G-ABC123")</script>'
        codes = extract_tracking_codes(html, "https://example.com")
        for code in codes:
            assert len(code.get("snippet", "")) <= 200
