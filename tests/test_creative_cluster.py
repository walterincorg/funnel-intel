"""Tests for creative similarity clustering pure logic.

No DB required — these exercise normalize_text, tokenize, compute_simhash,
hamming_distance, similarity, sign conversion, and _find_best_cluster.

The DB-touching cluster_ads_for_competitor is out of scope for unit tests;
it needs an integration harness (future work).
"""

from backend.worker.creative_cluster import (
    HAMMING_THRESHOLD,
    SIMHASH_BITS,
    _find_best_cluster,
    _from_signed,
    _to_signed,
    compute_simhash,
    hamming_distance,
    normalize_text,
    similarity,
    tokenize,
)


class TestNormalizeText:
    def test_none_returns_empty(self):
        assert normalize_text(None) == ""

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_lowercases(self):
        assert normalize_text("Start Your Journey") == "start your journey"

    def test_strips_punctuation(self):
        assert normalize_text("Ready? Let's go!") == "ready let s go"

    def test_collapses_whitespace(self):
        assert normalize_text("too    many     spaces") == "too many spaces"

    def test_trims_edges(self):
        assert normalize_text("  padded  ") == "padded"

    def test_unicode_words_preserved(self):
        # Unicode word chars (letters) should pass through, punctuation removed.
        assert normalize_text("café!") == "café"


class TestTokenize:
    def test_empty_string(self):
        assert tokenize("") == []

    def test_simple_split(self):
        assert tokenize("hello world") == ["hello", "world"]

    def test_multiple_spaces(self):
        # After normalize_text() there shouldn't be multiple spaces, but
        # tokenize should handle them anyway.
        assert tokenize("a  b") == ["a", "b"]


class TestComputeSimhash:
    def test_empty_ad_returns_zero(self):
        assert compute_simhash({}) == 0
        assert compute_simhash({"headline": "", "body_text": "", "cta": ""}) == 0
        assert compute_simhash({"headline": None, "body_text": None}) == 0

    def test_returns_unsigned_64bit(self):
        ad = {"headline": "Start your weight loss journey today", "body_text": "", "cta": "Learn More"}
        h = compute_simhash(ad)
        assert 0 <= h < (1 << SIMHASH_BITS)

    def test_deterministic(self):
        ad = {"headline": "Lose weight fast", "body_text": "Proven plan", "cta": "Start Now"}
        assert compute_simhash(ad) == compute_simhash(ad)

    def test_different_ads_different_hashes(self):
        ad_a = {
            "headline": "Lose weight fast with our proven program",
            "body_text": "Join thousands of happy customers today",
            "cta": "Start Now",
        }
        ad_b = {
            "headline": "Learn meditation in five minutes a day",
            "body_text": "Reduce stress with guided practice",
            "cta": "Try Free",
        }
        assert compute_simhash(ad_a) != compute_simhash(ad_b)

    def test_near_duplicate_ads_have_low_distance(self):
        # Same ad with one word swapped — the real-world "winner re-upload" case.
        ad_a = {
            "headline": "Start your weight loss journey today",
            "body_text": "Proven plan, thousands of success stories",
            "cta": "Learn More",
        }
        ad_b = {
            "headline": "Begin your weight loss journey today",
            "body_text": "Proven plan, thousands of success stories",
            "cta": "Learn More",
        }
        dist = hamming_distance(compute_simhash(ad_a), compute_simhash(ad_b))
        # Near-dupes should be well within the cluster threshold.
        assert dist <= HAMMING_THRESHOLD

    def test_distinct_ads_have_high_distance(self):
        ad_a = {
            "headline": "Lose weight fast with our workout plan",
            "body_text": "High intensity training delivers real results quickly",
            "cta": "Start Now",
        }
        ad_b = {
            "headline": "Master Spanish with AI-powered daily lessons",
            "body_text": "Speak a new language in thirty days of practice",
            "cta": "Try Free",
        }
        dist = hamming_distance(compute_simhash(ad_a), compute_simhash(ad_b))
        # Totally different content should be far above the threshold.
        assert dist > HAMMING_THRESHOLD * 2

    def test_punctuation_insensitive(self):
        ad_a = {"headline": "Ready? Set. Go!", "body_text": "", "cta": ""}
        ad_b = {"headline": "ready set go", "body_text": "", "cta": ""}
        assert compute_simhash(ad_a) == compute_simhash(ad_b)

    def test_case_insensitive(self):
        ad_a = {"headline": "BUY NOW", "body_text": "", "cta": ""}
        ad_b = {"headline": "buy now", "body_text": "", "cta": ""}
        assert compute_simhash(ad_a) == compute_simhash(ad_b)


class TestHammingDistance:
    def test_identical_is_zero(self):
        assert hamming_distance(0xDEADBEEF, 0xDEADBEEF) == 0

    def test_single_bit_flip(self):
        assert hamming_distance(0b0000, 0b0001) == 1

    def test_all_bits_different(self):
        mask = (1 << SIMHASH_BITS) - 1
        assert hamming_distance(0, mask) == SIMHASH_BITS

    def test_masks_to_64_bits(self):
        # Any bits above bit 63 should be masked out before counting.
        assert hamming_distance(1 << 70, 0) == 0


class TestSimilarity:
    def test_identical_is_one(self):
        assert similarity(0xFF, 0xFF) == 1.0

    def test_fully_different_is_zero(self):
        mask = (1 << SIMHASH_BITS) - 1
        assert similarity(0, mask) == 0.0

    def test_half_different_is_half(self):
        # 32 bits different out of 64 = 0.5 similarity.
        half_mask = (1 << 32) - 1
        assert similarity(0, half_mask) == 0.5


class TestSignedConversion:
    def test_zero_round_trip(self):
        assert _from_signed(_to_signed(0)) == 0

    def test_small_positive_round_trip(self):
        assert _from_signed(_to_signed(42)) == 42

    def test_int64_max_round_trip(self):
        val = (1 << 63) - 1
        assert _from_signed(_to_signed(val)) == val

    def test_above_int64_max_becomes_negative_and_roundtrips(self):
        val = 1 << 63  # just above signed int64 max
        signed = _to_signed(val)
        assert signed < 0
        assert _from_signed(signed) == val

    def test_full_unsigned_max_round_trip(self):
        val = (1 << 64) - 1
        assert _from_signed(_to_signed(val)) == val


class TestFindBestCluster:
    def test_no_clusters_returns_none(self):
        result, dist = _find_best_cluster(0xABCD, [])
        assert result is None

    def test_exact_match_within_threshold(self):
        target = 0xABCDEF0123456789
        clusters = [{"id": "c1", "centroid_simhash": _to_signed(target)}]
        result, dist = _find_best_cluster(target, clusters)
        assert result is not None
        assert result["id"] == "c1"
        assert dist == 0

    def test_outside_threshold_returns_none(self):
        # Centroid with all bits flipped from the target — max distance.
        target = 0
        far_centroid = (1 << SIMHASH_BITS) - 1
        clusters = [{"id": "c1", "centroid_simhash": _to_signed(far_centroid)}]
        result, dist = _find_best_cluster(target, clusters)
        assert result is None

    def test_picks_closest_when_multiple_in_range(self):
        target = 0b0000
        # c1 differs by 2 bits, c2 differs by 1 bit.
        clusters = [
            {"id": "c1", "centroid_simhash": _to_signed(0b0011)},
            {"id": "c2", "centroid_simhash": _to_signed(0b0001)},
        ]
        result, dist = _find_best_cluster(target, clusters)
        assert result is not None
        assert result["id"] == "c2"
        assert dist == 1

    def test_ignores_far_clusters_and_picks_close_one(self):
        target = 0b0000
        mask = (1 << SIMHASH_BITS) - 1
        clusters = [
            {"id": "far", "centroid_simhash": _to_signed(mask)},  # 64 bits off
            {"id": "near", "centroid_simhash": _to_signed(0b0010)},  # 1 bit off
        ]
        result, dist = _find_best_cluster(target, clusters)
        assert result is not None
        assert result["id"] == "near"


class TestEndToEndNearDuplicate:
    """The scenario this whole module exists to solve."""

    def test_word_swap_ad_clusters_with_original(self):
        """Two ads differing by one word should end up in the same cluster."""
        original = {
            "headline": "Ready to transform your body in thirty days",
            "body_text": "Join our proven program and see real results fast",
            "cta": "Start Now",
        }
        variant = {
            "headline": "Ready to transform your body in sixty days",
            "body_text": "Join our proven program and see real results fast",
            "cta": "Start Now",
        }

        original_hash = compute_simhash(original)
        variant_hash = compute_simhash(variant)

        # Put original in as a cluster centroid, then ask "where does variant go?"
        clusters = [{"id": "winner_cluster", "centroid_simhash": _to_signed(original_hash)}]
        result, dist = _find_best_cluster(variant_hash, clusters)

        assert result is not None
        assert result["id"] == "winner_cluster"
        assert dist <= HAMMING_THRESHOLD
