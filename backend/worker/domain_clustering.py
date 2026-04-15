"""Operator clustering — group competitors that share a GA or Pixel code."""

from __future__ import annotations
import logging
from collections import defaultdict

from backend.db import get_db

log = logging.getLogger(__name__)


def compute_clusters() -> int:
    """Recompute operator clusters from current GA/Pixel fingerprints.

    Two or more competitors sharing the same code form a cluster — strong
    signal that they're run by the same operator. Returns the cluster count.
    """
    db = get_db()

    rows = db.table("domain_fingerprints").select(
        "competitor_id, fingerprint_type, fingerprint_value"
    ).execute().data

    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        key = (row["fingerprint_type"], row["fingerprint_value"])
        groups[key].add(row["competitor_id"])

    shared = {k: v for k, v in groups.items() if len(v) >= 2}

    existing_clusters = db.table("operator_clusters").select(
        "id, fingerprint_type, fingerprint_value"
    ).execute().data
    existing_map = {
        (c["fingerprint_type"], c["fingerprint_value"]): c["id"]
        for c in existing_clusters
    }

    active_cluster_ids: set[str] = set()

    for (fp_type, fp_value), competitor_ids in shared.items():
        key = (fp_type, fp_value)

        if key in existing_map:
            cluster_id = existing_map[key]
            active_cluster_ids.add(cluster_id)
            db.table("cluster_members").delete().eq("cluster_id", cluster_id).execute()
        else:
            cluster = db.table("operator_clusters").insert({
                "fingerprint_type": fp_type,
                "fingerprint_value": fp_value,
            }).execute().data[0]
            cluster_id = cluster["id"]
            active_cluster_ids.add(cluster_id)

        for cid in competitor_ids:
            db.table("cluster_members").insert({
                "cluster_id": cluster_id,
                "competitor_id": cid,
            }).execute()

    for key, cluster_id in existing_map.items():
        if cluster_id not in active_cluster_ids:
            db.table("cluster_members").delete().eq("cluster_id", cluster_id).execute()
            db.table("operator_clusters").delete().eq("id", cluster_id).execute()
            log.info("Removed stale cluster for %s=%s", key[0], key[1])

    log.info("Clustering complete: %d clusters found", len(shared))
    return len(shared)
