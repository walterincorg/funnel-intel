"""Tests for operator clustering logic.

These tests validate the pure logic of clustering, not the DB operations.
We test the grouping algorithm in isolation.
"""

from collections import defaultdict


# Extracted clustering logic for unit testing (mirrors domain_clustering.py)
CONFIDENCE_MAP = {
    "google_analytics": "high",
    "facebook_pixel": "high",
    "gtm": "medium",
    "hosting": "low",
    "tech_stack": "low",
}


def _compute_clusters_pure(fingerprints: list[dict]) -> list[dict]:
    """Pure function version of compute_clusters for testing."""
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for fp in fingerprints:
        key = (fp["type"], fp["value"])
        groups[key].add(fp["competitor_id"])

    clusters = []
    for (fp_type, fp_value), comp_ids in groups.items():
        if len(comp_ids) >= 2:
            clusters.append({
                "fingerprint_type": fp_type,
                "fingerprint_value": fp_value,
                "competitor_ids": sorted(comp_ids),
                "confidence": CONFIDENCE_MAP.get(fp_type, "low"),
            })
    return clusters


class TestComputeClusters:
    def test_shared_ga_creates_cluster(self):
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-SHARED"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-SHARED"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert len(clusters) == 1
        assert set(clusters[0]["competitor_ids"]) == {"a", "b"}
        assert clusters[0]["confidence"] == "high"

    def test_no_shared_codes_no_clusters(self):
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-AAA"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-BBB"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert clusters == []

    def test_shared_hosting_only_is_low_confidence(self):
        fingerprints = [
            {"competitor_id": "a", "type": "hosting", "value": "Shopify"},
            {"competitor_id": "b", "type": "hosting", "value": "Shopify"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert len(clusters) == 1
        assert clusters[0]["confidence"] == "low"

    def test_shared_pixel_is_high_confidence(self):
        fingerprints = [
            {"competitor_id": "a", "type": "facebook_pixel", "value": "12345"},
            {"competitor_id": "b", "type": "facebook_pixel", "value": "12345"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert len(clusters) == 1
        assert clusters[0]["confidence"] == "high"

    def test_shared_gtm_is_medium_confidence(self):
        fingerprints = [
            {"competitor_id": "a", "type": "gtm", "value": "GTM-ABC"},
            {"competitor_id": "b", "type": "gtm", "value": "GTM-ABC"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert len(clusters) == 1
        assert clusters[0]["confidence"] == "medium"

    def test_cluster_removed_when_code_no_longer_shared(self):
        # Only one competitor has the code now
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-SHARED"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-NEW"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert clusters == []

    def test_multiple_clusters(self):
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-1"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-1"},
            {"competitor_id": "c", "type": "facebook_pixel", "value": "PX-2"},
            {"competitor_id": "d", "type": "facebook_pixel", "value": "PX-2"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert len(clusters) == 2

    def test_three_competitors_same_code(self):
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-SHARED"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-SHARED"},
            {"competitor_id": "c", "type": "google_analytics", "value": "G-SHARED"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert len(clusters) == 1
        assert len(clusters[0]["competitor_ids"]) == 3

    def test_mixed_shared_and_unique(self):
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-SHARED"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-SHARED"},
            {"competitor_id": "c", "type": "google_analytics", "value": "G-UNIQUE"},
            {"competitor_id": "a", "type": "hosting", "value": "Shopify"},
            {"competitor_id": "c", "type": "hosting", "value": "Shopify"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert len(clusters) == 2
        ga_cluster = next(c for c in clusters if c["fingerprint_type"] == "google_analytics")
        host_cluster = next(c for c in clusters if c["fingerprint_type"] == "hosting")
        assert set(ga_cluster["competitor_ids"]) == {"a", "b"}
        assert set(host_cluster["competitor_ids"]) == {"a", "c"}
