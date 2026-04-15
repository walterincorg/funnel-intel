"""SimHash-based creative similarity clustering.

Groups near-duplicate ads together so the synthesis layer sees "3 angles × 7
variants" instead of "21 new ads." Clusters are scoped per competitor — two
different brands converging on similar copy is a pattern for the synthesis
layer to surface, not noise to dedup away.

Algorithm:
  1. Normalize ad text (headline + body_text + cta) — lowercase, strip
     punctuation, collapse whitespace.
  2. Tokenize into words.
  3. Compute 64-bit SimHash: each token contributes +1/-1 to each of 64 bit
     counters based on its SHA-256 hash. Final bit = 1 if counter > 0 else 0.
  4. For each new ad, scan existing clusters for the same competitor. Join
     the closest cluster with hamming distance <= HAMMING_THRESHOLD. If none,
     create a new cluster.

Similarity metric:
  similarity = 1 - (hamming_distance / 64)
  threshold  = HAMMING_THRESHOLD / 64  (e.g., 5/64 ≈ 92% similar)

Storage:
  SimHash is an unsigned 64-bit integer in Python. Postgres bigint is signed
  64-bit. Convert at the boundary via _to_signed / _from_signed.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone

from backend.db import get_db

log = logging.getLogger(__name__)

SIMHASH_BITS = 64
# Hamming distance <= 12 (~81% similarity) = same cluster.
# Calibrated empirically for short DTC ad text (headline + body + cta, typically
# 15-30 tokens). On short documents, single-word swaps produce 5-12 bit
# differences in SimHash due to low token counts per bit counter, while
# genuinely distinct ads cluster around 28-35 bits of distance. A threshold of
# 12 catches winner re-uploads without collapsing distinct angles together.
HAMMING_THRESHOLD = 12

# Regex for normalization: keep letters, numbers, and whitespace.
_NORMALIZE_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


# --- Text processing -------------------------------------------------------


def normalize_text(text: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    lowered = text.lower()
    stripped = _NORMALIZE_RE.sub(" ", lowered)
    collapsed = _WHITESPACE_RE.sub(" ", stripped).strip()
    return collapsed


def _ad_text_features(ad: dict) -> str:
    """Concatenate the ad's text features into one normalized string."""
    parts = [
        ad.get("headline") or "",
        ad.get("body_text") or "",
        ad.get("cta") or "",
    ]
    return normalize_text(" ".join(parts))


def tokenize(text: str) -> list[str]:
    """Split normalized text on whitespace."""
    if not text:
        return []
    return text.split()


# --- SimHash ---------------------------------------------------------------


def _token_hash(token: str) -> int:
    """Stable 64-bit hash for a token using SHA-256's first 8 bytes."""
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def compute_simhash(ad: dict) -> int:
    """Compute the 64-bit unsigned SimHash of an ad's text features.

    Returns 0 for ads with no usable text (empty headline + body + cta).
    """
    tokens = tokenize(_ad_text_features(ad))
    if not tokens:
        return 0

    counters = [0] * SIMHASH_BITS
    for token in tokens:
        token_bits = _token_hash(token)
        for bit in range(SIMHASH_BITS):
            if token_bits & (1 << bit):
                counters[bit] += 1
            else:
                counters[bit] -= 1

    simhash = 0
    for bit in range(SIMHASH_BITS):
        if counters[bit] > 0:
            simhash |= 1 << bit
    return simhash


def hamming_distance(a: int, b: int) -> int:
    """Count the number of differing bits between two 64-bit integers."""
    return bin((a ^ b) & ((1 << SIMHASH_BITS) - 1)).count("1")


def similarity(a: int, b: int) -> float:
    """Return similarity in [0, 1] where 1 = identical, 0 = maximally different."""
    return 1.0 - hamming_distance(a, b) / SIMHASH_BITS


# --- Signed/unsigned conversion (Python unsigned <-> Postgres bigint) ------

_INT64_MAX = (1 << 63) - 1
_INT64_MOD = 1 << 64


def _to_signed(unsigned_64: int) -> int:
    """Convert a Python unsigned 64-bit int to a signed int for Postgres bigint."""
    return unsigned_64 - _INT64_MOD if unsigned_64 > _INT64_MAX else unsigned_64


def _from_signed(signed_64: int) -> int:
    """Convert a Postgres bigint back to a Python unsigned 64-bit int."""
    return signed_64 + _INT64_MOD if signed_64 < 0 else signed_64


# --- Cluster assignment ----------------------------------------------------


def _find_best_cluster(
    ad_simhash: int, existing_clusters: list[dict]
) -> tuple[dict | None, int]:
    """Scan existing clusters, return (best_cluster, best_hamming_distance).

    best_cluster is None if no cluster is within HAMMING_THRESHOLD.
    """
    best: dict | None = None
    best_dist = SIMHASH_BITS + 1
    for cluster in existing_clusters:
        centroid = _from_signed(cluster["centroid_simhash"])
        dist = hamming_distance(ad_simhash, centroid)
        if dist < best_dist:
            best_dist = dist
            best = cluster
    if best is None or best_dist > HAMMING_THRESHOLD:
        return None, best_dist
    return best, best_dist


def cluster_ads_for_competitor(
    competitor_id: str,
    ads: list[dict],
    ad_id_map: dict[str, str],
) -> dict[str, int]:
    """Assign each ad to a cluster (existing or new) for one competitor.

    Args:
      competitor_id: competitor UUID.
      ads: normalized ad dicts (must have meta_ad_id + text features).
      ad_id_map: meta_ad_id -> db ad UUID, from the batch upsert step.

    Returns:
      Stats dict with keys: processed, new_clusters, joined_existing, skipped.
    """
    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()

    stats = {"processed": 0, "new_clusters": 0, "joined_existing": 0, "skipped": 0}

    # Load existing clusters for this competitor once.
    clusters_res = (
        db.table("creative_clusters")
        .select("id, centroid_simhash, member_count")
        .eq("competitor_id", competitor_id)
        .execute()
    )
    existing_clusters: list[dict] = clusters_res.data or []

    # Load existing memberships to avoid re-clustering ads we've already seen.
    ad_db_ids = [ad_id_map[a["meta_ad_id"]] for a in ads if a.get("meta_ad_id") in ad_id_map]
    already_clustered: set[str] = set()
    if ad_db_ids:
        members_res = (
            db.table("ad_cluster_members")
            .select("ad_id")
            .in_("ad_id", ad_db_ids)
            .execute()
        )
        already_clustered = {row["ad_id"] for row in (members_res.data or [])}

    new_members: list[dict] = []

    for ad in ads:
        meta_id = ad.get("meta_ad_id")
        if not meta_id:
            stats["skipped"] += 1
            continue
        ad_db_id = ad_id_map.get(meta_id)
        if not ad_db_id:
            stats["skipped"] += 1
            continue
        if ad_db_id in already_clustered:
            # Already assigned — skip. Re-clustering on copy edits is a future
            # concern; for now the first assignment sticks.
            continue

        ad_simhash = compute_simhash(ad)
        if ad_simhash == 0:
            # No usable text — don't cluster, don't crash.
            stats["skipped"] += 1
            continue

        best_cluster, dist = _find_best_cluster(ad_simhash, existing_clusters)

        if best_cluster is not None:
            cluster_id = best_cluster["id"]
            sim = 1.0 - dist / SIMHASH_BITS
            new_members.append({
                "ad_id": ad_db_id,
                "cluster_id": cluster_id,
                "simhash": _to_signed(ad_simhash),
                "similarity": sim,
                "assigned_at": now_iso,
            })
            # Optimistic in-memory increment so subsequent ads in this batch
            # see the updated member_count if we care to use it later.
            best_cluster["member_count"] += 1
            # Persist the member_count + last_seen_at bump.
            try:
                db.table("creative_clusters").update({
                    "member_count": best_cluster["member_count"],
                    "last_seen_at": now_iso,
                }).eq("id", cluster_id).execute()
            except Exception:
                log.exception("Failed to bump member_count for cluster %s", cluster_id)
            stats["joined_existing"] += 1
        else:
            # Create a new cluster with this ad as representative.
            try:
                created = db.table("creative_clusters").insert({
                    "competitor_id": competitor_id,
                    "centroid_simhash": _to_signed(ad_simhash),
                    "representative_ad_id": ad_db_id,
                    "member_count": 1,
                    "first_seen_at": now_iso,
                    "last_seen_at": now_iso,
                }).execute()
            except Exception:
                log.exception("Failed to create new cluster for ad %s", ad_db_id)
                stats["skipped"] += 1
                continue

            cluster_row = created.data[0]
            existing_clusters.append(cluster_row)
            new_members.append({
                "ad_id": ad_db_id,
                "cluster_id": cluster_row["id"],
                "simhash": _to_signed(ad_simhash),
                "similarity": 1.0,
                "assigned_at": now_iso,
            })
            stats["new_clusters"] += 1

        stats["processed"] += 1

    # Batch insert memberships in one call.
    if new_members:
        try:
            db.table("ad_cluster_members").insert(new_members).execute()
        except Exception:
            log.exception(
                "Batch insert of %d cluster memberships failed for %s; "
                "falling back to per-row inserts",
                len(new_members), competitor_id,
            )
            for row in new_members:
                try:
                    db.table("ad_cluster_members").insert(row).execute()
                except Exception:
                    log.exception("Per-row member insert failed for ad %s", row["ad_id"])

    return stats
