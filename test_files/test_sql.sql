-- test_sql.sql
--
-- Holistic SQL showcase — multi-tenant SaaS analytics platform.
--
-- Covers: DDL (tables, constraints, indexes, sequences), DML (insert,
-- update, delete, upsert), views, materialised views, CTEs, recursive CTEs,
-- window functions (rank, lag, lead, ntile, running totals), stored
-- procedures, functions, triggers, full-text search, JSON operations,
-- pivot-style queries, cohort analysis, funnel analysis, and partitioning.


-- ===========================================================================
-- EXTENSIONS
-- ===========================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;           -- trigram fuzzy search


-- ===========================================================================
-- SCHEMA
-- ===========================================================================

-- Tenants (organisations using the SaaS platform)
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id    UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    slug         VARCHAR(63)  NOT NULL UNIQUE,
    name         VARCHAR(255) NOT NULL,
    plan         VARCHAR(20)  NOT NULL DEFAULT 'free'
                              CHECK (plan IN ('free','starter','pro','enterprise')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    cancelled_at TIMESTAMPTZ
);

-- Users
CREATE TABLE IF NOT EXISTS users (
    user_id      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID         NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    email        VARCHAR(320) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    role         VARCHAR(20)  NOT NULL DEFAULT 'member'
                              CHECK (role IN ('owner','admin','member','guest')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ,
    metadata     JSONB        NOT NULL DEFAULT '{}',
    UNIQUE (tenant_id, email)
);

-- Events (user actions tracked by the platform)
CREATE TABLE IF NOT EXISTS events (
    event_id     BIGSERIAL    PRIMARY KEY,
    tenant_id    UUID         NOT NULL REFERENCES tenants(tenant_id),
    user_id      UUID         REFERENCES users(user_id),
    session_id   VARCHAR(64),
    event_name   VARCHAR(100) NOT NULL,
    occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    properties   JSONB        NOT NULL DEFAULT '{}'
) PARTITION BY RANGE (occurred_at);

-- Monthly partitions
CREATE TABLE IF NOT EXISTS events_2024_01 PARTITION OF events
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
CREATE TABLE IF NOT EXISTS events_2024_02 PARTITION OF events
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
CREATE TABLE IF NOT EXISTS events_default PARTITION OF events DEFAULT;

-- Subscriptions
CREATE TABLE IF NOT EXISTS subscriptions (
    sub_id       UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID         NOT NULL REFERENCES tenants(tenant_id),
    plan         VARCHAR(20)  NOT NULL,
    starts_at    DATE         NOT NULL,
    ends_at      DATE,
    mrr_cents    INT          NOT NULL DEFAULT 0,
    cancelled_at TIMESTAMPTZ,
    CONSTRAINT positive_mrr CHECK (mrr_cents >= 0)
);

-- Feature flags
CREATE TABLE IF NOT EXISTS feature_flags (
    flag_id      SERIAL       PRIMARY KEY,
    flag_key     VARCHAR(100) NOT NULL UNIQUE,
    enabled      BOOLEAN      NOT NULL DEFAULT FALSE,
    rollout_pct  NUMERIC(5,2) NOT NULL DEFAULT 0
                              CHECK (rollout_pct BETWEEN 0 AND 100),
    tenant_ids   UUID[]       NOT NULL DEFAULT '{}',
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    log_id       BIGSERIAL    PRIMARY KEY,
    tenant_id    UUID,
    user_id      UUID,
    action       VARCHAR(100) NOT NULL,
    target_table VARCHAR(100),
    target_id    TEXT,
    old_values   JSONB,
    new_values   JSONB,
    occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_events_tenant_time   ON events (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_user_time     ON events (user_id,   occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_name          ON events (event_name);
CREATE INDEX IF NOT EXISTS idx_events_props         ON events USING GIN (properties);
CREATE INDEX IF NOT EXISTS idx_users_email_trgm     ON users  USING GIN (email gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_users_metadata       ON users  USING GIN (metadata);


-- ===========================================================================
-- VIEWS
-- ===========================================================================

CREATE OR REPLACE VIEW v_active_tenants AS
SELECT
    t.tenant_id,
    t.name,
    t.plan,
    t.created_at,
    COUNT(DISTINCT u.user_id)       AS user_count,
    MAX(u.last_seen_at)             AS last_activity,
    SUM(s.mrr_cents) / 100.0       AS mrr_dollars
FROM  tenants t
LEFT JOIN users         u ON u.tenant_id = t.tenant_id
LEFT JOIN subscriptions s ON s.tenant_id = t.tenant_id AND s.cancelled_at IS NULL
WHERE t.cancelled_at IS NULL
GROUP BY t.tenant_id, t.name, t.plan, t.created_at;


-- Materialised view for heavy dashboard queries
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_daily_event_counts AS
SELECT
    tenant_id,
    event_name,
    DATE_TRUNC('day', occurred_at)::DATE AS day,
    COUNT(*)                             AS event_count,
    COUNT(DISTINCT user_id)              AS unique_users,
    COUNT(DISTINCT session_id)           AS sessions
FROM events
GROUP BY 1, 2, 3
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_daily_events
    ON mv_daily_event_counts (tenant_id, event_name, day);

-- Refresh command (run on a schedule):
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_daily_event_counts;


-- ===========================================================================
-- FUNCTIONS
-- ===========================================================================

-- Check whether a tenant has a specific feature flag enabled
CREATE OR REPLACE FUNCTION is_feature_enabled(
    p_tenant_id UUID,
    p_flag_key  VARCHAR
)
RETURNS BOOLEAN
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_flag feature_flags%ROWTYPE;
BEGIN
    SELECT * INTO v_flag FROM feature_flags WHERE flag_key = p_flag_key;
    IF NOT FOUND THEN RETURN FALSE; END IF;
    IF NOT v_flag.enabled THEN RETURN FALSE; END IF;
    IF p_tenant_id = ANY(v_flag.tenant_ids) THEN RETURN TRUE; END IF;
    RETURN (RANDOM() * 100) < v_flag.rollout_pct;
END;
$$;


-- Compute MRR for a given month
CREATE OR REPLACE FUNCTION monthly_mrr(p_month DATE)
RETURNS NUMERIC
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(SUM(mrr_cents), 0) / 100.0
    FROM   subscriptions
    WHERE  starts_at <= p_month
      AND  (ends_at IS NULL OR ends_at > p_month)
      AND  cancelled_at IS NULL;
$$;


-- ===========================================================================
-- STORED PROCEDURE
-- ===========================================================================

CREATE OR REPLACE PROCEDURE provision_tenant(
    p_slug        VARCHAR,
    p_name        VARCHAR,
    p_owner_email VARCHAR,
    p_owner_name  VARCHAR,
    p_plan        VARCHAR DEFAULT 'free',
    OUT o_tenant_id UUID,
    OUT o_user_id   UUID
)
LANGUAGE plpgsql AS $$
DECLARE
    v_mrr_cents INT := CASE p_plan
        WHEN 'starter'    THEN 1900
        WHEN 'pro'        THEN 4900
        WHEN 'enterprise' THEN 19900
        ELSE 0
    END;
BEGIN
    INSERT INTO tenants (slug, name, plan)
    VALUES (p_slug, p_name, p_plan)
    RETURNING tenant_id INTO o_tenant_id;

    INSERT INTO users (tenant_id, email, display_name, role)
    VALUES (o_tenant_id, p_owner_email, p_owner_name, 'owner')
    RETURNING user_id INTO o_user_id;

    IF v_mrr_cents > 0 THEN
        INSERT INTO subscriptions (tenant_id, plan, starts_at, mrr_cents)
        VALUES (o_tenant_id, p_plan, CURRENT_DATE, v_mrr_cents);
    END IF;

    INSERT INTO audit_log (tenant_id, user_id, action, target_table, target_id, new_values)
    VALUES (
        o_tenant_id, o_user_id, 'tenant.provisioned', 'tenants',
        o_tenant_id::TEXT,
        jsonb_build_object('slug', p_slug, 'plan', p_plan)
    );

    RAISE NOTICE 'Provisioned tenant % (%) with owner %', p_name, o_tenant_id, p_owner_email;
END;
$$;


-- ===========================================================================
-- TRIGGERS
-- ===========================================================================

CREATE OR REPLACE FUNCTION fn_update_last_seen()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.event_name = 'session.start' AND NEW.user_id IS NOT NULL THEN
        UPDATE users SET last_seen_at = NEW.occurred_at
        WHERE user_id = NEW.user_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_update_last_seen ON events;
CREATE TRIGGER trg_update_last_seen
AFTER INSERT ON events
FOR EACH ROW EXECUTE FUNCTION fn_update_last_seen();


CREATE OR REPLACE FUNCTION fn_audit_users()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit_log (tenant_id, action, target_table, target_id, old_values, new_values)
    VALUES (
        COALESCE(NEW.tenant_id, OLD.tenant_id),
        TG_OP || '.users',
        'users',
        COALESCE(NEW.user_id, OLD.user_id)::TEXT,
        CASE TG_OP WHEN 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
        CASE TG_OP WHEN 'DELETE' THEN NULL ELSE to_jsonb(NEW) END
    );
    RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_users ON users;
CREATE TRIGGER trg_audit_users
AFTER INSERT OR UPDATE OR DELETE ON users
FOR EACH ROW EXECUTE FUNCTION fn_audit_users();


-- ===========================================================================
-- ANALYTICS QUERIES
-- ===========================================================================

-- ── 1. MRR movement — new, expansion, churn ──────────────────────────────
WITH monthly AS (
    SELECT
        DATE_TRUNC('month', starts_at)::DATE AS month,
        SUM(mrr_cents)                       AS new_mrr
    FROM subscriptions
    WHERE cancelled_at IS NULL
    GROUP BY 1
),
churned AS (
    SELECT
        DATE_TRUNC('month', cancelled_at)::DATE AS month,
        SUM(mrr_cents)                          AS churned_mrr
    FROM subscriptions
    WHERE cancelled_at IS NOT NULL
    GROUP BY 1
)
SELECT
    COALESCE(m.month, c.month)           AS month,
    COALESCE(m.new_mrr,     0) / 100.0   AS new_mrr,
    COALESCE(c.churned_mrr, 0) / 100.0   AS churned_mrr,
    (COALESCE(m.new_mrr, 0) - COALESCE(c.churned_mrr, 0)) / 100.0 AS net_mrr
FROM monthly m
FULL OUTER JOIN churned c ON c.month = m.month
ORDER BY month;


-- ── 2. Retention — weekly cohorts ────────────────────────────────────────
WITH cohorts AS (
    SELECT
        user_id,
        DATE_TRUNC('week', MIN(occurred_at))::DATE AS cohort_week
    FROM events
    WHERE event_name = 'session.start'
    GROUP BY user_id
),
weekly_activity AS (
    SELECT
        c.user_id,
        c.cohort_week,
        DATE_TRUNC('week', e.occurred_at)::DATE AS activity_week,
        (DATE_TRUNC('week', e.occurred_at) - c.cohort_week::TIMESTAMPTZ)
            / INTERVAL '7 days'                   AS week_number
    FROM cohorts c
    JOIN events e ON e.user_id = c.user_id AND e.event_name = 'session.start'
)
SELECT
    cohort_week,
    week_number::INT,
    COUNT(DISTINCT user_id)  AS retained_users
FROM weekly_activity
GROUP BY 1, 2
ORDER BY 1, 2;


-- ── 3. Funnel analysis ───────────────────────────────────────────────────
WITH funnel_steps AS (
    SELECT
        user_id,
        session_id,
        MAX(CASE WHEN event_name = 'page.view'       THEN 1 ELSE 0 END) AS step1,
        MAX(CASE WHEN event_name = 'signup.start'    THEN 1 ELSE 0 END) AS step2,
        MAX(CASE WHEN event_name = 'signup.complete' THEN 1 ELSE 0 END) AS step3,
        MAX(CASE WHEN event_name = 'onboard.finish'  THEN 1 ELSE 0 END) AS step4
    FROM events
    WHERE occurred_at >= NOW() - INTERVAL '30 days'
    GROUP BY user_id, session_id
)
SELECT
    SUM(step1)                        AS page_views,
    SUM(step2)                        AS started_signup,
    SUM(step3)                        AS completed_signup,
    SUM(step4)                        AS onboarded,
    ROUND(SUM(step2)::NUMERIC / NULLIF(SUM(step1), 0) * 100, 1) AS pct_started,
    ROUND(SUM(step3)::NUMERIC / NULLIF(SUM(step2), 0) * 100, 1) AS pct_converted,
    ROUND(SUM(step4)::NUMERIC / NULLIF(SUM(step3), 0) * 100, 1) AS pct_onboarded
FROM funnel_steps;


-- ── 4. Window functions — ranking, running totals, lag ───────────────────
WITH tenant_revenue AS (
    SELECT
        t.tenant_id,
        t.name,
        t.plan,
        DATE_TRUNC('month', s.starts_at)::DATE   AS month,
        SUM(s.mrr_cents) / 100.0                 AS mrr
    FROM tenants t
    JOIN subscriptions s ON s.tenant_id = t.tenant_id
    GROUP BY 1, 2, 3, 4
)
SELECT
    tenant_id,
    name,
    plan,
    month,
    mrr,
    LAG(mrr)  OVER (PARTITION BY tenant_id ORDER BY month)  AS prev_mrr,
    LEAD(mrr) OVER (PARTITION BY tenant_id ORDER BY month)  AS next_mrr,
    SUM(mrr)  OVER (PARTITION BY tenant_id ORDER BY month
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_mrr,
    RANK()    OVER (PARTITION BY month ORDER BY mrr DESC)  AS rank_this_month,
    NTILE(4)  OVER (ORDER BY mrr DESC)                     AS quartile
FROM tenant_revenue
ORDER BY month, rank_this_month;


-- ── 5. Recursive CTE — organisational hierarchy ──────────────────────────
WITH RECURSIVE org_tree AS (
    SELECT
        user_id,
        display_name,
        role,
        tenant_id,
        0 AS depth,
        display_name::TEXT AS path
    FROM users
    WHERE role = 'owner'

    UNION ALL

    SELECT
        u.user_id,
        u.display_name,
        u.role,
        u.tenant_id,
        ot.depth + 1,
        ot.path || ' > ' || u.display_name
    FROM users u
    JOIN org_tree ot ON ot.tenant_id = u.tenant_id AND ot.depth < 5
    WHERE u.role IN ('admin', 'member')
)
SELECT depth, role, display_name, path
FROM org_tree
ORDER BY path;


-- ── 6. JSON operations ───────────────────────────────────────────────────
SELECT
    user_id,
    email,
    metadata->>'plan'                               AS plan_from_meta,
    metadata->'preferences'->>'theme'               AS theme,
    jsonb_array_length(metadata->'roles')           AS role_count,
    metadata @> '{"verified": true}'::jsonb         AS is_verified,
    jsonb_object_keys(metadata)                     AS meta_key
FROM users
WHERE metadata != '{}'
ORDER BY user_id;


-- ── 7. Upsert and bulk operations ────────────────────────────────────────
INSERT INTO feature_flags (flag_key, enabled, rollout_pct)
VALUES
    ('dark_mode',        TRUE,  100),
    ('new_dashboard',    TRUE,   50),
    ('ai_suggestions',   FALSE,   0),
    ('bulk_export',      TRUE,   25)
ON CONFLICT (flag_key) DO UPDATE
    SET enabled     = EXCLUDED.enabled,
        rollout_pct = EXCLUDED.rollout_pct,
        updated_at  = NOW();


-- ── 8. Full-text and fuzzy search ────────────────────────────────────────
SELECT
    user_id,
    email,
    display_name,
    similarity(email, 'alice@example.com')  AS fuzzy_score
FROM users
WHERE email % 'alice@example.com'          -- trigram similarity operator
ORDER BY fuzzy_score DESC
LIMIT 10;
