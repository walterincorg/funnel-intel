"""Deterministic pattern extraction from ingested data.

Runs as part of the weekly synthesis loop. Reads from ads, creative_clusters,
scan_runs, pricing_snapshots, domain_changes, and discovered_domains, and
writes structured observations to the patterns table.

Six detectors, one per pattern_type:
  - proven_winner  : a creative cluster that has been running for N+ days
                     with multiple variants — a validated angle.
  - killed_test    : a creative cluster whose members have all gone inactive
                     within a short window — an angle that was tried and
                     rejected by the market.
  - ad_angle_shift : a competitor whose active-ad distribution has pivoted
                     from one cluster to another in the last N days.
  - funnel_change  : a competitor's latest scan run shows structural drift
                     against its baseline (uses existing drift_details).
  - price_move     : a competitor's pricing snapshots show a meaningful
                     price delta within the lookback window.
  - launch_signal  : a discovered domain linked to an existing competitor
                     cluster within the lookback window — points to a new
                     vertical or sister brand about to launch.

Each detector emits pattern dicts with evidence_refs (concrete row citations)
and a confidence score from score_confidence(). Orchestration runs all six
and batch-upserts into the patterns table on signature_hash conflict.

Design notes:
  - Detectors are deterministic: signature_hash is a stable sha256 of the
    pattern type plus its core identifying evidence, so re-running is a
    no-op (bumps last_seen_at + observation_count).
  - DB errors inside a detector never break the others. Each detector runs
    in its own try/except in the orchestrator.
  - No LLM calls from this module. Narrative generation (llm.py + prompts)
    is a follow-up; the deterministic evidence is what the synthesis loop
    feeds to the ship list generator.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.db import get_db

log = logging.getLogger(__name__)

# --- Pattern type constants --------------------------------------------------

PATTERN_FUNNEL_CHANGE = "funnel_change"
PATTERN_AD_ANGLE_SHIFT = "ad_angle_shift"
PATTERN_PRICE_MOVE = "price_move"
PATTERN_LAUNCH_SIGNAL = "launch_signal"
PATTERN_PROVEN_WINNER = "proven_winner"
PATTERN_KILLED_TEST = "killed_test"

VALID_PATTERN_TYPES = {
    PATTERN_FUNNEL_CHANGE,
    PATTERN_AD_ANGLE_SHIFT,
    PATTERN_PRICE_MOVE,
    PATTERN_LAUNCH_SIGNAL,
    PATTERN_PROVEN_WINNER,
    PATTERN_KILLED_TEST,
}

# --- Tuning knobs (detector thresholds) --------------------------------------

PROVEN_WINNER_MIN_AGE_DAYS = 30
PROVEN_WINNER_MIN_MEMBERS = 2

KILLED_TEST_MAX_AGE_DAYS = 14
KILLED_TEST_MIN_MEMBERS = 3

AD_ANGLE_SHIFT_LOOKBACK_DAYS = 14
AD_ANGLE_SHIFT_MIN_DELTA = 0.5  # 50% of cluster's prior active member count

FUNNEL_CHANGE_LOOKBACK_DAYS = 30
FUNNEL_CHANGE_MIN_DRIFT_LEVEL = {"medium", "high", "critical"}

PRICE_MOVE_LOOKBACK_DAYS = 60
PRICE_MOVE_MIN_PCT = 0.15  # 15% change counts as a meaningful move

LAUNCH_SIGNAL_LOOKBACK_DAYS = 30

# --- Confidence scoring ------------------------------------------------------


def score_confidence(
    competitor_count: int,
    evidence_count: int,
    time_window_days: int,
    base: float = 5.0,
) -> float:
    """Deterministic confidence score in [0, 10].

    Rubric:
      - base: 5 (moderate trust in a single observation)
      - +2 if observed in 2+ competitors
      - +1 if observed in 3+ competitors
      - +1 if time window >= 14 days
      - +min(evidence_count / 5, 2) for evidence volume
    """
    score = float(base)
    if competitor_count >= 2:
        score += 2.0
    if competitor_count >= 3:
        score += 1.0
    if time_window_days >= 14:
        score += 1.0
    score += min(evidence_count / 5.0, 2.0)
    return min(score, 10.0)


# --- Signature hashing (for idempotent upsert) -------------------------------


def _canonical_key(parts: list[str]) -> str:
    """Sort + join string parts for stable hashing."""
    return "|".join(sorted(str(p) for p in parts if p is not None))


def compute_signature(pattern_type: str, key_parts: list[str]) -> str:
    """Stable 32-char signature for pattern dedup.

    The signature is derived from the pattern_type + its core identifying
    evidence. Two runs that observe the same structural pattern produce the
    same signature, so the upsert is a no-op (bumps last_seen_at).
    """
    if pattern_type not in VALID_PATTERN_TYPES:
        raise ValueError(f"Unknown pattern_type: {pattern_type!r}")
    canonical = f"{pattern_type}::{_canonical_key(key_parts)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# --- Evidence ref helpers ----------------------------------------------------

EVIDENCE_AD = "ad"
EVIDENCE_SCAN_RUN = "scan_run"
EVIDENCE_CLUSTER = "cluster"
EVIDENCE_PRICING = "pricing_snapshot"
EVIDENCE_DOMAIN_CHANGE = "domain_change"
EVIDENCE_DISCOVERED_DOMAIN = "discovered_domain"


def _ref(ref_type: str, ref_id: str, label: str | None = None) -> dict:
    """Build an evidence ref dict."""
    out: dict[str, Any] = {"type": ref_type, "id": ref_id}
    if label:
        out["label"] = label
    return out


# --- Price extraction helper -------------------------------------------------

# Price deltas come from pricing_snapshots.plans, which is a heterogeneous
# jsonb blob (competitors structure plans differently). We extract a single
# representative numeric price per snapshot — the lowest positive number we
# can find inside plans — and compare snapshots on that. Crude but stable.


def _extract_min_price(plans: Any) -> float | None:
    """Pull the lowest positive price from a plans jsonb blob.

    The plans field shape varies per competitor. This is a best-effort
    flattener: recurse into dicts/lists, collect numeric values that look
    like prices (between 0.01 and 10000), return the minimum.
    """
    if plans is None:
        return None

    candidates: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, (int, float)):
            f = float(node)
            if 0.01 <= f <= 10000:
                candidates.append(f)
        elif isinstance(node, str):
            # Parse "$49.99", "49.99", "49,99 €", etc. Require a decimal
            # separator between digits so we reject bare integers with unit
            # labels ("7 days" should NOT parse as 7.0).
            cleaned = "".join(c for c in node if c.isdigit() or c in ".,")
            if not cleaned:
                return
            cleaned = cleaned.replace(",", ".")
            # Must have at least one dot with digits on both sides.
            if "." not in cleaned:
                return
            first_dot = cleaned.index(".")
            if first_dot == 0 or first_dot == len(cleaned) - 1:
                return
            if not (cleaned[first_dot - 1].isdigit() and cleaned[first_dot + 1].isdigit()):
                return
            # Handle cases with multiple dots from mixed separators.
            if cleaned.count(".") > 1:
                parts = cleaned.split(".")
                cleaned = parts[0] + "." + "".join(parts[1:])
            try:
                f = float(cleaned)
                if 0.01 <= f <= 10000:
                    candidates.append(f)
            except ValueError:
                return

    walk(plans)
    return min(candidates) if candidates else None


def _pct_change(from_val: float, to_val: float) -> float:
    """Signed percent change. Returns 0 if from_val is 0."""
    if from_val == 0:
        return 0.0
    return (to_val - from_val) / from_val


# --- Detectors ---------------------------------------------------------------


def detect_proven_winners(now: datetime | None = None) -> list[dict]:
    """Clusters that have run for 30+ days with multiple variants.

    Evidence: the cluster itself plus its members (ad IDs).
    Signature: cluster_id (one pattern per winning cluster).
    """
    now = now or datetime.now(timezone.utc)
    threshold = (now - timedelta(days=PROVEN_WINNER_MIN_AGE_DAYS)).isoformat()
    db = get_db()

    try:
        res = (
            db.table("creative_clusters")
            .select("id,competitor_id,member_count,first_seen_at,last_seen_at,representative_ad_id")
            .lte("first_seen_at", threshold)
            .gte("member_count", PROVEN_WINNER_MIN_MEMBERS)
            .execute()
        )
    except Exception:
        log.exception("detect_proven_winners: query failed")
        return []

    patterns: list[dict] = []
    for cluster in res.data or []:
        cluster_id = cluster["id"]
        competitor_id = cluster["competitor_id"]
        member_count = cluster["member_count"]
        age_days = _days_between(cluster["first_seen_at"], now)

        refs = [_ref(EVIDENCE_CLUSTER, cluster_id, label=f"{member_count} variants, {age_days}d old")]
        if cluster.get("representative_ad_id"):
            refs.append(_ref(EVIDENCE_AD, cluster["representative_ad_id"], label="representative"))

        confidence = score_confidence(
            competitor_count=1,
            evidence_count=member_count,
            time_window_days=age_days,
            base=6.0,  # winners get a slightly higher base since age + variants is strong signal
        )

        patterns.append({
            "pattern_type": PATTERN_PROVEN_WINNER,
            "signature_hash": compute_signature(PATTERN_PROVEN_WINNER, [cluster_id]),
            "observed_in_competitors": [competitor_id],
            "confidence": confidence,
            "evidence_refs": refs,
            "headline": f"Proven winner: cluster with {member_count} variants running {age_days}d",
            "metadata": {"cluster_id": cluster_id, "member_count": member_count, "age_days": age_days},
        })

    return patterns


def detect_killed_tests(now: datetime | None = None) -> list[dict]:
    """Clusters with 3+ members where all are now INACTIVE and the cluster is young.

    Evidence: the cluster + all member ads.
    Signature: cluster_id.
    """
    now = now or datetime.now(timezone.utc)
    age_threshold = (now - timedelta(days=KILLED_TEST_MAX_AGE_DAYS)).isoformat()
    db = get_db()

    try:
        clusters_res = (
            db.table("creative_clusters")
            .select("id,competitor_id,member_count,first_seen_at")
            .gte("first_seen_at", age_threshold)
            .gte("member_count", KILLED_TEST_MIN_MEMBERS)
            .execute()
        )
    except Exception:
        log.exception("detect_killed_tests: cluster query failed")
        return []

    candidate_clusters = clusters_res.data or []
    if not candidate_clusters:
        return []

    cluster_ids = [c["id"] for c in candidate_clusters]

    try:
        members_res = (
            db.table("ad_cluster_members")
            .select("ad_id,cluster_id")
            .in_("cluster_id", cluster_ids)
            .execute()
        )
    except Exception:
        log.exception("detect_killed_tests: members query failed")
        return []

    members_by_cluster: dict[str, list[str]] = {}
    for row in members_res.data or []:
        members_by_cluster.setdefault(row["cluster_id"], []).append(row["ad_id"])

    all_ad_ids = [aid for ads in members_by_cluster.values() for aid in ads]
    if not all_ad_ids:
        return []

    try:
        ads_res = (
            db.table("ads")
            .select("id,status")
            .in_("id", all_ad_ids)
            .execute()
        )
    except Exception:
        log.exception("detect_killed_tests: ads query failed")
        return []

    status_map = {row["id"]: row.get("status") for row in ads_res.data or []}

    patterns: list[dict] = []
    for cluster in candidate_clusters:
        cluster_id = cluster["id"]
        member_ids = members_by_cluster.get(cluster_id, [])
        if len(member_ids) < KILLED_TEST_MIN_MEMBERS:
            continue
        # All members must be INACTIVE (or absent from the ads table).
        if not all(status_map.get(aid) == "INACTIVE" for aid in member_ids):
            continue

        age_days = _days_between(cluster["first_seen_at"], now)
        refs = [_ref(EVIDENCE_CLUSTER, cluster_id, label=f"{len(member_ids)} ads, all inactive")]
        for ad_id in member_ids[:10]:  # cap ref list size
            refs.append(_ref(EVIDENCE_AD, ad_id))

        confidence = score_confidence(
            competitor_count=1,
            evidence_count=len(member_ids),
            time_window_days=age_days,
        )

        patterns.append({
            "pattern_type": PATTERN_KILLED_TEST,
            "signature_hash": compute_signature(PATTERN_KILLED_TEST, [cluster_id]),
            "observed_in_competitors": [cluster["competitor_id"]],
            "confidence": confidence,
            "evidence_refs": refs,
            "headline": f"Killed test: {len(member_ids)}-variant cluster went fully inactive in {age_days}d",
            "metadata": {"cluster_id": cluster_id, "member_count": len(member_ids), "age_days": age_days},
        })

    return patterns


def detect_ad_angle_shifts(now: datetime | None = None) -> list[dict]:
    """Per-competitor shift in active-ad distribution across clusters.

    A "shift" is detected when, within the lookback window, a cluster that
    was dominant sees its active-member count drop by AD_ANGLE_SHIFT_MIN_DELTA
    while another cluster on the same competitor sees its count rise by the
    same proportion.

    Evidence: the two clusters (old + new) + a sample of each.
    Signature: competitor_id + old_cluster_id + new_cluster_id.
    """
    now = now or datetime.now(timezone.utc)
    threshold = (now - timedelta(days=AD_ANGLE_SHIFT_LOOKBACK_DAYS)).isoformat()
    db = get_db()

    try:
        clusters_res = (
            db.table("creative_clusters")
            .select("id,competitor_id,member_count,first_seen_at,last_seen_at")
            .execute()
        )
    except Exception:
        log.exception("detect_ad_angle_shifts: cluster query failed")
        return []

    by_competitor: dict[str, list[dict]] = {}
    for c in clusters_res.data or []:
        by_competitor.setdefault(c["competitor_id"], []).append(c)

    patterns: list[dict] = []

    for competitor_id, clusters in by_competitor.items():
        # A "declining" cluster: last_seen_at BEFORE the threshold (hasn't been
        # touched recently), but member_count is high — used to be big.
        # A "rising" cluster: first_seen_at AFTER the threshold, member_count
        # comparable to the declining one.
        declining = [
            c for c in clusters
            if c.get("last_seen_at") and c["last_seen_at"] < threshold
            and c["member_count"] >= 2
        ]
        rising = [
            c for c in clusters
            if c.get("first_seen_at") and c["first_seen_at"] >= threshold
            and c["member_count"] >= 2
        ]
        if not declining or not rising:
            continue

        # Pair each declining cluster with the largest rising cluster whose
        # size is at least AD_ANGLE_SHIFT_MIN_DELTA of the declining size.
        for old in declining:
            threshold_size = max(1, int(old["member_count"] * AD_ANGLE_SHIFT_MIN_DELTA))
            candidates = [r for r in rising if r["member_count"] >= threshold_size]
            if not candidates:
                continue
            new = max(candidates, key=lambda r: r["member_count"])

            refs = [
                _ref(EVIDENCE_CLUSTER, old["id"], label=f"declining, {old['member_count']} variants"),
                _ref(EVIDENCE_CLUSTER, new["id"], label=f"rising, {new['member_count']} variants"),
            ]

            confidence = score_confidence(
                competitor_count=1,
                evidence_count=old["member_count"] + new["member_count"],
                time_window_days=AD_ANGLE_SHIFT_LOOKBACK_DAYS,
            )

            patterns.append({
                "pattern_type": PATTERN_AD_ANGLE_SHIFT,
                "signature_hash": compute_signature(
                    PATTERN_AD_ANGLE_SHIFT, [competitor_id, old["id"], new["id"]]
                ),
                "observed_in_competitors": [competitor_id],
                "confidence": confidence,
                "evidence_refs": refs,
                "headline": f"Angle shift: {old['member_count']}-variant cluster cooling, {new['member_count']}-variant cluster rising",
                "metadata": {
                    "old_cluster_id": old["id"],
                    "new_cluster_id": new["id"],
                    "old_size": old["member_count"],
                    "new_size": new["member_count"],
                },
            })

    return patterns


def detect_funnel_changes(now: datetime | None = None) -> list[dict]:
    """Scan runs whose drift_level is medium+ against their baseline.

    Uses the existing drift_details captured in loop.py.

    Evidence: the scan run + its baseline run.
    Signature: scan_run_id (one pattern per drifted run).
    Cross-competitor boost: if 2+ competitors show funnel_change patterns
    in the same extraction window, each gets a small confidence bump.
    """
    now = now or datetime.now(timezone.utc)
    threshold = (now - timedelta(days=FUNNEL_CHANGE_LOOKBACK_DAYS)).isoformat()
    db = get_db()

    try:
        res = (
            db.table("scan_runs")
            .select("id,competitor_id,drift_level,drift_details,baseline_run_id,completed_at")
            .gte("completed_at", threshold)
            .eq("status", "completed")
            .execute()
        )
    except Exception:
        log.exception("detect_funnel_changes: query failed")
        return []

    drifted = [
        r for r in (res.data or [])
        if (r.get("drift_level") or "").lower() in FUNNEL_CHANGE_MIN_DRIFT_LEVEL
    ]
    if not drifted:
        return []

    unique_competitors = {r["competitor_id"] for r in drifted}
    cross_competitor = len(unique_competitors)

    patterns: list[dict] = []
    for run in drifted:
        refs = [_ref(EVIDENCE_SCAN_RUN, run["id"], label=f"drift: {run.get('drift_level')}")]
        if run.get("baseline_run_id"):
            refs.append(_ref(EVIDENCE_SCAN_RUN, run["baseline_run_id"], label="baseline"))

        drift_changes = run.get("drift_details") or []
        evidence_count = len(drift_changes) if isinstance(drift_changes, list) else 1

        confidence = score_confidence(
            competitor_count=cross_competitor,
            evidence_count=evidence_count,
            time_window_days=FUNNEL_CHANGE_LOOKBACK_DAYS,
        )

        patterns.append({
            "pattern_type": PATTERN_FUNNEL_CHANGE,
            "signature_hash": compute_signature(PATTERN_FUNNEL_CHANGE, [run["id"]]),
            "observed_in_competitors": [run["competitor_id"]],
            "confidence": confidence,
            "evidence_refs": refs,
            "headline": f"Funnel drift ({run.get('drift_level')}): {evidence_count} step change(s)",
            "metadata": {
                "scan_run_id": run["id"],
                "drift_level": run.get("drift_level"),
                "change_count": evidence_count,
                "cross_competitor_context": cross_competitor,
            },
        })

    return patterns


def detect_price_moves(now: datetime | None = None) -> list[dict]:
    """Per-competitor pricing deltas over the lookback window.

    Compares each competitor's oldest-in-window and newest-in-window snapshots.
    Emits a pattern if the minimum price changed by >= PRICE_MOVE_MIN_PCT.

    Evidence: the two snapshots.
    Signature: competitor_id + from_snapshot_id + to_snapshot_id.
    """
    now = now or datetime.now(timezone.utc)
    threshold = (now - timedelta(days=PRICE_MOVE_LOOKBACK_DAYS)).isoformat()
    db = get_db()

    try:
        res = (
            db.table("pricing_snapshots")
            .select("id,competitor_id,plans,created_at")
            .gte("created_at", threshold)
            .order("created_at")
            .execute()
        )
    except Exception:
        log.exception("detect_price_moves: query failed")
        return []

    by_competitor: dict[str, list[dict]] = {}
    for row in res.data or []:
        by_competitor.setdefault(row["competitor_id"], []).append(row)

    patterns: list[dict] = []

    for competitor_id, snaps in by_competitor.items():
        if len(snaps) < 2:
            continue
        # Snapshots are already ordered by created_at ascending.
        oldest = snaps[0]
        newest = snaps[-1]
        if oldest["id"] == newest["id"]:
            continue

        from_price = _extract_min_price(oldest.get("plans"))
        to_price = _extract_min_price(newest.get("plans"))
        if from_price is None or to_price is None:
            continue

        pct = _pct_change(from_price, to_price)
        if abs(pct) < PRICE_MOVE_MIN_PCT:
            continue

        refs = [
            _ref(EVIDENCE_PRICING, oldest["id"], label=f"was ${from_price:.2f}"),
            _ref(EVIDENCE_PRICING, newest["id"], label=f"now ${to_price:.2f}"),
        ]

        confidence = score_confidence(
            competitor_count=1,
            evidence_count=len(snaps),
            time_window_days=PRICE_MOVE_LOOKBACK_DAYS,
        )

        direction = "up" if pct > 0 else "down"
        patterns.append({
            "pattern_type": PATTERN_PRICE_MOVE,
            "signature_hash": compute_signature(
                PATTERN_PRICE_MOVE, [competitor_id, oldest["id"], newest["id"]]
            ),
            "observed_in_competitors": [competitor_id],
            "confidence": confidence,
            "evidence_refs": refs,
            "headline": f"Price move {direction}: ${from_price:.2f} -> ${to_price:.2f} ({pct * 100:+.1f}%)",
            "metadata": {
                "from_price": from_price,
                "to_price": to_price,
                "pct_change": pct,
                "direction": direction,
                "from_snapshot_id": oldest["id"],
                "to_snapshot_id": newest["id"],
            },
        })

    return patterns


def detect_launch_signals(now: datetime | None = None) -> list[dict]:
    """New domains discovered in the lookback window, linked to existing competitors.

    Evidence: the discovered domain + any linked competitor ids.
    Signature: discovered_domain.id.
    """
    now = now or datetime.now(timezone.utc)
    threshold = (now - timedelta(days=LAUNCH_SIGNAL_LOOKBACK_DAYS)).isoformat()
    db = get_db()

    try:
        domains_res = (
            db.table("discovered_domains")
            .select("id,domain,discovery_source,discovery_reason,linked_fingerprint_value,first_seen_at,relevance,status")
            .gte("first_seen_at", threshold)
            .neq("status", "dismissed")
            .execute()
        )
    except Exception:
        log.exception("detect_launch_signals: discovered_domains query failed")
        return []

    domains = domains_res.data or []
    if not domains:
        return []

    domain_ids = [d["id"] for d in domains]

    try:
        links_res = (
            db.table("domain_competitor_links")
            .select("domain_id,competitor_id")
            .in_("domain_id", domain_ids)
            .execute()
        )
    except Exception:
        log.exception("detect_launch_signals: domain_competitor_links query failed")
        links_res = None

    links_by_domain: dict[str, list[str]] = {}
    if links_res:
        for row in links_res.data or []:
            links_by_domain.setdefault(row["domain_id"], []).append(row["competitor_id"])

    patterns: list[dict] = []
    for dom in domains:
        linked_competitors = links_by_domain.get(dom["id"], [])
        # Only emit if we could tie the domain to at least one tracked operator.
        if not linked_competitors:
            continue

        refs = [
            _ref(EVIDENCE_DISCOVERED_DOMAIN, dom["id"], label=dom.get("domain")),
        ]

        confidence = score_confidence(
            competitor_count=len(linked_competitors),
            evidence_count=1,
            time_window_days=LAUNCH_SIGNAL_LOOKBACK_DAYS,
            base=6.0,  # high trust: this is a concrete new-domain observation
        )
        # Relevance bumps confidence a little.
        relevance = (dom.get("relevance") or "").lower()
        if relevance == "high":
            confidence = min(confidence + 1.0, 10.0)

        patterns.append({
            "pattern_type": PATTERN_LAUNCH_SIGNAL,
            "signature_hash": compute_signature(PATTERN_LAUNCH_SIGNAL, [dom["id"]]),
            "observed_in_competitors": linked_competitors,
            "confidence": confidence,
            "evidence_refs": refs,
            "headline": f"Launch signal: {dom.get('domain')} linked to {len(linked_competitors)} tracked operator(s)",
            "metadata": {
                "discovered_domain_id": dom["id"],
                "domain": dom.get("domain"),
                "discovery_source": dom.get("discovery_source"),
                "relevance": dom.get("relevance"),
            },
        })

    return patterns


# --- Persistence -------------------------------------------------------------


def persist_patterns(patterns: list[dict]) -> int:
    """Upsert patterns on signature_hash conflict. Returns row count written.

    Idempotent by signature: a repeat observation bumps last_seen_at and
    observation_count instead of creating a duplicate. The upsert relies on
    the UNIQUE(signature_hash) constraint from the migration.
    """
    if not patterns:
        return 0

    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Look up existing rows to preserve first_seen_at and bump observation_count.
    signatures = [p["signature_hash"] for p in patterns]
    try:
        existing_res = (
            db.table("patterns")
            .select("signature_hash,first_seen_at,observation_count")
            .in_("signature_hash", signatures)
            .execute()
        )
    except Exception:
        log.exception("persist_patterns: existing lookup failed")
        return 0

    existing_by_sig = {row["signature_hash"]: row for row in (existing_res.data or [])}

    rows: list[dict] = []
    for p in patterns:
        sig = p["signature_hash"]
        prior = existing_by_sig.get(sig)
        if prior:
            rows.append({
                "pattern_type": p["pattern_type"],
                "signature_hash": sig,
                "observed_in_competitors": p["observed_in_competitors"],
                "first_seen_at": prior["first_seen_at"],
                "last_seen_at": now_iso,
                "observation_count": (prior.get("observation_count") or 1) + 1,
                "confidence": p["confidence"],
                "evidence_refs": p["evidence_refs"],
                "headline": p.get("headline"),
                "metadata": p.get("metadata"),
            })
        else:
            rows.append({
                "pattern_type": p["pattern_type"],
                "signature_hash": sig,
                "observed_in_competitors": p["observed_in_competitors"],
                "first_seen_at": now_iso,
                "last_seen_at": now_iso,
                "observation_count": 1,
                "confidence": p["confidence"],
                "evidence_refs": p["evidence_refs"],
                "headline": p.get("headline"),
                "metadata": p.get("metadata"),
            })

    try:
        db.table("patterns").upsert(rows, on_conflict="signature_hash").execute()
    except Exception:
        log.exception("persist_patterns: upsert failed")
        return 0

    return len(rows)


# --- Orchestration -----------------------------------------------------------


def extract_all_patterns(now: datetime | None = None) -> dict:
    """Run every detector in isolation and persist the resulting patterns.

    One detector failing does not affect the others. Returns a stats dict
    suitable for logging into synthesis_runs later.
    """
    now = now or datetime.now(timezone.utc)

    detectors: list[tuple[str, Any]] = [
        (PATTERN_PROVEN_WINNER, detect_proven_winners),
        (PATTERN_KILLED_TEST, detect_killed_tests),
        (PATTERN_AD_ANGLE_SHIFT, detect_ad_angle_shifts),
        (PATTERN_FUNNEL_CHANGE, detect_funnel_changes),
        (PATTERN_PRICE_MOVE, detect_price_moves),
        (PATTERN_LAUNCH_SIGNAL, detect_launch_signals),
    ]

    all_patterns: list[dict] = []
    per_type_counts: dict[str, int] = {}
    failures: list[str] = []

    for pattern_type, fn in detectors:
        try:
            patterns = fn(now=now)
        except Exception:
            log.exception("Detector %s failed", pattern_type)
            failures.append(pattern_type)
            per_type_counts[pattern_type] = 0
            continue
        per_type_counts[pattern_type] = len(patterns)
        all_patterns.extend(patterns)

    persisted = persist_patterns(all_patterns)

    log.info(
        "Pattern extraction: %d detectors, %d patterns found, %d persisted, %d failures",
        len(detectors), len(all_patterns), persisted, len(failures),
    )

    return {
        "detectors_run": len(detectors),
        "patterns_found": len(all_patterns),
        "patterns_persisted": persisted,
        "failures": failures,
        "per_type": per_type_counts,
    }


# --- Small helpers -----------------------------------------------------------


def _days_between(iso_ts: str | None, now: datetime) -> int:
    """Whole-day age of an ISO timestamp. Returns 0 if unparseable."""
    if not iso_ts:
        return 0
    try:
        # Handle both "Z" suffix and "+00:00" tz offsets from Postgres.
        s = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (now - dt).days)
    except (ValueError, TypeError):
        return 0


# --- Serialization (for API / debug) -----------------------------------------


def pattern_to_json(pattern: dict) -> str:
    """Human-readable JSON dump of a pattern dict (for logging / debug)."""
    return json.dumps(pattern, default=str, indent=2)
