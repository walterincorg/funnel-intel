-- STEP 10: feedback loop columns on ship_list_items.
--
-- shipping_at: the first time an item transitioned into the `shipping`
-- (or `shipped`) status. This is the clock the feedback loop measures
-- against: N days after shipping_at we prompt the founder to record
-- won/lost/inconclusive/not_tested. Separate from shipped_at (which
-- records when they explicitly marked it complete).
--
-- outcome_alerted_at: when the feedback loop sent the "record an
-- outcome" alert for this item. Populated once and never reset, so the
-- worker doesn't re-alert on every poll cycle.

ALTER TABLE ship_list_items
  ADD COLUMN IF NOT EXISTS shipping_at timestamptz;

ALTER TABLE ship_list_items
  ADD COLUMN IF NOT EXISTS outcome_alerted_at timestamptz;

-- Index the feedback-loop predicate: "items that entered the shipping
-- lifecycle more than N days ago and haven't been alerted yet."
CREATE INDEX IF NOT EXISTS idx_ship_list_items_feedback_due
  ON ship_list_items(shipping_at)
  WHERE shipping_at IS NOT NULL AND outcome_alerted_at IS NULL;
