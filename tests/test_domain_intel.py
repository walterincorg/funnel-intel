"""Tests for GA + Pixel extraction regex patterns."""

from unittest.mock import patch, MagicMock
from backend.worker.domain_intel import extract_tracking_codes, _fetch_gtm_codes


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

    def test_gtm_extracted(self):
        html = '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>'
        codes = extract_tracking_codes(html, "https://example.com")
        assert any(c["type"] == "gtm" and c["id"] == "GTM-ABC123" for c in codes)

    def test_gtm_from_inline_snippet(self):
        html = """<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
        new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
        j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
        'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
        })(window,document,'script','dataLayer','GTM-5WVFRTZ');</script>"""
        codes = extract_tracking_codes(html, "https://example.com")
        assert any(c["type"] == "gtm" and c["id"] == "GTM-5WVFRTZ" for c in codes)

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

    def test_extract_ga_from_script_src(self):
        html = '<script src="https://www.googletagmanager.com/gtag/js?id=G-XYZ789"></script>'
        codes = extract_tracking_codes(html, "https://example.com")
        assert any(c["type"] == "google_analytics" and c["id"] == "G-XYZ789" for c in codes)

    def test_extract_fb_pixel_from_noscript_img(self):
        html = '<noscript><img src="https://www.facebook.com/tr?id=554433221100&ev=PageView" /></noscript>'
        codes = extract_tracking_codes(html, "https://example.com")
        assert any(c["type"] == "facebook_pixel" and c["id"] == "554433221100" for c in codes)


class TestGTMFollowThrough:
    @patch("backend.worker.domain_intel.requests.get")
    def test_fetches_ga_from_gtm_container(self, mock_get):
        """When page has GTM, fetch the container JS and extract GA IDs."""
        container_js = 'var config = {"G-HIDDEN123": {"target": "G-HIDDEN123"}};'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = container_js
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        html = '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"></script>'
        codes = _fetch_gtm_codes(html)
        assert any(c["type"] == "google_analytics" and c["id"] == "G-HIDDEN123" for c in codes)

    @patch("backend.worker.domain_intel.requests.get")
    def test_fetches_pixel_from_gtm_container(self, mock_get):
        """When GTM container has fbq init, extract the pixel ID."""
        container_js = """function(){fbq('init','998877665544')}"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = container_js
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        html = '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-XYZ789"></script>'
        codes = _fetch_gtm_codes(html)
        assert any(c["type"] == "facebook_pixel" and c["id"] == "998877665544" for c in codes)

    def test_no_gtm_returns_empty(self):
        html = "<html><body>No GTM here</body></html>"
        codes = _fetch_gtm_codes(html)
        assert codes == []
