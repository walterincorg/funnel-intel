"""Tests for operator clustering logic.

Pure-function version of `compute_clusters` for unit testing, mirroring
the real grouping logic in domain_clustering.py.
"""

from collections import defaultdict


def _compute_clusters_pure(fingerprints: list[dict]) -> list[dict]:
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

    def test_no_shared_codes_no_clusters(self):
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-AAA"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-BBB"},
        ]
        assert _compute_clusters_pure(fingerprints) == []

    def test_shared_pixel_creates_cluster(self):
        fingerprints = [
            {"competitor_id": "a", "type": "facebook_pixel", "value": "12345"},
            {"competitor_id": "b", "type": "facebook_pixel", "value": "12345"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert len(clusters) == 1
        assert clusters[0]["fingerprint_type"] == "facebook_pixel"

    def test_cluster_removed_when_code_no_longer_shared(self):
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-SHARED"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-NEW"},
        ]
        assert _compute_clusters_pure(fingerprints) == []

    def test_multiple_clusters(self):
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-1"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-1"},
            {"competitor_id": "c", "type": "facebook_pixel", "value": "PX-2"},
            {"competitor_id": "d", "type": "facebook_pixel", "value": "PX-2"},
        ]
        assert len(_compute_clusters_pure(fingerprints)) == 2

    def test_three_competitors_same_code(self):
        fingerprints = [
            {"competitor_id": "a", "type": "google_analytics", "value": "G-SHARED"},
            {"competitor_id": "b", "type": "google_analytics", "value": "G-SHARED"},
            {"competitor_id": "c", "type": "google_analytics", "value": "G-SHARED"},
        ]
        clusters = _compute_clusters_pure(fingerprints)
        assert len(clusters) == 1
        assert len(clusters[0]["competitor_ids"]) == 3
