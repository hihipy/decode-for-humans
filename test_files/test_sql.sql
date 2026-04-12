-- test_sql.sql
--
-- E-commerce analytics database for a multi-region retail platform.
--
-- Covers: schema creation, constraints, indexes, views, CTEs,
-- window functions, stored procedures, triggers, and complex
-- reporting queries used by the business intelligence team.


-- ===========================================================================
-- SCHEMA SETUP
-- ===========================================================================

CREATE TABLE IF NOT EXISTS regions (
    region_id   SERIAL       PRIMARY KEY,
    region_name VARCHAR(100) NOT NULL UNIQUE,
    currency    CHAR(3)      NOT NULL DEFAULT 'USD',
    timezone    VARCHAR(50)  NOT NULL DEFAULT 'UTC'
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id   SERIAL        PRIMARY KEY,
    email         VARCHAR(255)  NOT NULL UNIQUE,
    first_name    VARCHAR(100)  NOT NULL,
    last_name     VARCHAR(100)  NOT NULL,
    region_id     INT           NOT NULL REFERENCES regions(region_id),
    signup_date   DATE          NOT NULL DEFAULT CURRENT_DATE,
    is_active     BOOLEAN       NOT NULL DEFAULT TRUE,
    lifetime_spend NUMERIC(12,2) NOT NULL DEFAULT 0.00
);

CREATE TABLE IF NOT EXISTS product_categories (
    category_id   SERIAL      PRIMARY KEY,
    category_name VARCHAR(100) NOT NULL UNIQUE,
    parent_id     INT          REFERENCES product_categories(category_id)
);

CREATE TABLE IF NOT EXISTS products (
    product_id    SERIAL        PRIMARY KEY,
    sku           VARCHAR(50)   NOT NULL UNIQUE,
    product_name  VARCHAR(255)  NOT NULL,
    category_id   INT           NOT NULL REFERENCES product_categories(category_id),
    unit_price    NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0),
    stock_qty     INT           NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    is_active     BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    order_id      SERIAL        PRIMARY KEY,
    customer_id   INT           NOT NULL REFERENCES customers(customer_id),
    region_id     INT           NOT NULL REFERENCES regions(region_id),
    order_status  VARCHAR(20)   NOT NULL DEFAULT 'pending'
                                CHECK (order_status IN
                                  ('pending','confirmed','shipped','delivered','cancelled')),
    placed_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    shipped_at    TIMESTAMPTZ,
    delivered_at  TIMESTAMPTZ,
    total_amount  NUMERIC(12,2) NOT NULL DEFAULT 0.00
);

CREATE TABLE IF NOT EXISTS order_lines (
    line_id       SERIAL        PRIMARY KEY,
    order_id      INT           NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id    INT           NOT NULL REFERENCES products(product_id),
    quantity      INT           NOT NULL CHECK (quantity > 0),
    unit_price    NUMERIC(10,2) NOT NULL,
    line_total    NUMERIC(12,2) GENERATED ALWAYS AS (quantity * unit_price) STORED
);

CREATE TABLE IF NOT EXISTS promotions (
    promo_id      SERIAL       PRIMARY KEY,
    promo_code    VARCHAR(50)  NOT NULL UNIQUE,
    discount_pct  NUMERIC(5,2) NOT NULL CHECK (discount_pct BETWEEN 0 AND 100),
    starts_at     DATE         NOT NULL,
    ends_at       DATE         NOT NULL,
    usage_limit   INT,
    times_used    INT          NOT NULL DEFAULT 0,
    CHECK (ends_at > starts_at)
);

-- Indexes for commonly filtered and joined columns
CREATE INDEX IF NOT EXISTS idx_orders_customer   ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_status     ON orders(order_status);
CREATE INDEX IF NOT EXISTS idx_orders_placed     ON orders(placed_at DESC);
CREATE INDEX IF NOT EXISTS idx_order_lines_order ON order_lines(order_id);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_customers_region  ON customers(region_id);


-- ===========================================================================
-- VIEWS
-- ===========================================================================

-- Denormalised order summary used by reporting dashboards
CREATE OR REPLACE VIEW v_order_summary AS
SELECT
    o.order_id,
    o.placed_at,
    o.order_status,
    c.customer_id,
    c.email,
    c.first_name || ' ' || c.last_name  AS customer_name,
    r.region_name,
    r.currency,
    COUNT(ol.line_id)                   AS line_count,
    SUM(ol.line_total)                  AS calculated_total,
    o.total_amount                      AS recorded_total,
    EXTRACT(EPOCH FROM (o.shipped_at - o.placed_at)) / 3600
                                        AS hours_to_ship
FROM orders o
JOIN customers c  ON c.customer_id = o.customer_id
JOIN regions r    ON r.region_id   = o.region_id
JOIN order_lines ol ON ol.order_id = o.order_id
GROUP BY
    o.order_id, o.placed_at, o.order_status,
    c.customer_id, c.email, c.first_name, c.last_name,
    r.region_name, r.currency, o.shipped_at, o.total_amount;


-- ===========================================================================
-- STORED PROCEDURE — place an order
-- ===========================================================================

CREATE OR REPLACE PROCEDURE place_order(
    p_customer_id  INT,
    p_items        JSONB       -- [{"product_id": 1, "quantity": 2}, ...]
)
LANGUAGE plpgsql AS $$
DECLARE
    v_order_id   INT;
    v_region_id  INT;
    v_item       JSONB;
    v_product_id INT;
    v_qty        INT;
    v_price      NUMERIC(10,2);
    v_total      NUMERIC(12,2) := 0;
BEGIN
    -- Look up the customer's region
    SELECT region_id INTO v_region_id
    FROM customers
    WHERE customer_id = p_customer_id AND is_active = TRUE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Customer % not found or inactive', p_customer_id;
    END IF;

    -- Create the order header
    INSERT INTO orders (customer_id, region_id, order_status)
    VALUES (p_customer_id, v_region_id, 'pending')
    RETURNING order_id INTO v_order_id;

    -- Process each line item
    FOR v_item IN SELECT * FROM jsonb_array_elements(p_items)
    LOOP
        v_product_id := (v_item->>'product_id')::INT;
        v_qty        := (v_item->>'quantity')::INT;

        -- Lock the row to prevent overselling in concurrent transactions
        SELECT unit_price INTO v_price
        FROM products
        WHERE product_id = v_product_id
          AND is_active  = TRUE
          AND stock_qty  >= v_qty
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Product % unavailable or insufficient stock for qty %',
                v_product_id, v_qty;
        END IF;

        INSERT INTO order_lines (order_id, product_id, quantity, unit_price)
        VALUES (v_order_id, v_product_id, v_qty, v_price);

        UPDATE products
        SET stock_qty = stock_qty - v_qty
        WHERE product_id = v_product_id;

        v_total := v_total + (v_price * v_qty);
    END LOOP;

    -- Write the final total back to the order header
    UPDATE orders
    SET total_amount = v_total,
        order_status = 'confirmed'
    WHERE order_id = v_order_id;

    -- Keep the customer's lifetime spend up to date
    UPDATE customers
    SET lifetime_spend = lifetime_spend + v_total
    WHERE customer_id = p_customer_id;

    RAISE NOTICE 'Order % placed successfully. Total: %', v_order_id, v_total;
END;
$$;


-- ===========================================================================
-- TRIGGER — log status changes
-- ===========================================================================

CREATE TABLE IF NOT EXISTS order_status_log (
    log_id       SERIAL      PRIMARY KEY,
    order_id     INT         NOT NULL,
    old_status   VARCHAR(20),
    new_status   VARCHAR(20) NOT NULL,
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION fn_log_order_status_change()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.order_status IS DISTINCT FROM NEW.order_status THEN
        INSERT INTO order_status_log (order_id, old_status, new_status)
        VALUES (NEW.order_id, OLD.order_status, NEW.order_status);
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_order_status ON orders;
CREATE TRIGGER trg_order_status
AFTER UPDATE OF order_status ON orders
FOR EACH ROW EXECUTE FUNCTION fn_log_order_status_change();


-- ===========================================================================
-- ANALYTICS QUERIES
-- ===========================================================================

-- ── 1. Month-over-month revenue with growth rate ─────────────────────────

WITH monthly_revenue AS (
    SELECT
        DATE_TRUNC('month', placed_at)::DATE AS month,
        SUM(total_amount)                    AS revenue
    FROM orders
    WHERE order_status NOT IN ('cancelled', 'pending')
    GROUP BY 1
),
revenue_with_lag AS (
    SELECT
        month,
        revenue,
        LAG(revenue) OVER (ORDER BY month) AS prev_revenue
    FROM monthly_revenue
)
SELECT
    month,
    revenue,
    prev_revenue,
    ROUND(
        (revenue - prev_revenue) / NULLIF(prev_revenue, 0) * 100,
        2
    ) AS growth_pct
FROM revenue_with_lag
ORDER BY month DESC;


-- ── 2. Customer lifetime value segmentation ──────────────────────────────

WITH customer_stats AS (
    SELECT
        c.customer_id,
        c.email,
        c.lifetime_spend,
        COUNT(DISTINCT o.order_id)    AS order_count,
        MAX(o.placed_at)              AS last_order_date,
        MIN(o.placed_at)              AS first_order_date,
        AVG(o.total_amount)           AS avg_order_value
    FROM customers c
    LEFT JOIN orders o
        ON o.customer_id = c.customer_id
        AND o.order_status = 'delivered'
    GROUP BY c.customer_id, c.email, c.lifetime_spend
),
segmented AS (
    SELECT
        *,
        CASE
            WHEN lifetime_spend >= 5000 THEN 'VIP'
            WHEN lifetime_spend >= 1000 THEN 'Loyal'
            WHEN lifetime_spend >= 250  THEN 'Regular'
            WHEN order_count    = 0     THEN 'Never Ordered'
            ELSE 'Occasional'
        END AS segment,
        NTILE(4) OVER (ORDER BY lifetime_spend) AS spend_quartile
    FROM customer_stats
)
SELECT
    segment,
    spend_quartile,
    COUNT(*)                            AS customer_count,
    ROUND(AVG(lifetime_spend), 2)       AS avg_lifetime_spend,
    ROUND(AVG(avg_order_value), 2)      AS avg_order_value,
    ROUND(AVG(order_count), 1)          AS avg_orders
FROM segmented
GROUP BY segment, spend_quartile
ORDER BY avg_lifetime_spend DESC;


-- ── 3. Top products by revenue with category rollup ─────────────────────

WITH product_revenue AS (
    SELECT
        p.product_id,
        p.product_name,
        p.sku,
        pc.category_name,
        SUM(ol.quantity)              AS units_sold,
        SUM(ol.line_total)            AS total_revenue,
        COUNT(DISTINCT o.customer_id) AS unique_buyers
    FROM order_lines ol
    JOIN orders o    ON o.order_id    = ol.order_id
    JOIN products p  ON p.product_id  = ol.product_id
    JOIN product_categories pc ON pc.category_id = p.category_id
    WHERE o.order_status = 'delivered'
    GROUP BY p.product_id, p.product_name, p.sku, pc.category_name
),
ranked AS (
    SELECT
        *,
        RANK() OVER (
            PARTITION BY category_name
            ORDER BY total_revenue DESC
        ) AS rank_in_category,
        ROUND(
            total_revenue / SUM(total_revenue) OVER () * 100,
            2
        ) AS revenue_share_pct
    FROM product_revenue
)
SELECT *
FROM ranked
WHERE rank_in_category <= 5
ORDER BY category_name, rank_in_category;


-- ── 4. Cohort retention — customers ordering in month N who reorder ──────

WITH cohorts AS (
    SELECT
        customer_id,
        DATE_TRUNC('month', MIN(placed_at))::DATE AS cohort_month
    FROM orders
    WHERE order_status NOT IN ('cancelled', 'pending')
    GROUP BY customer_id
),
cohort_orders AS (
    SELECT
        c.customer_id,
        c.cohort_month,
        DATE_TRUNC('month', o.placed_at)::DATE AS order_month,
        (DATE_PART('year',  DATE_TRUNC('month', o.placed_at)) -
         DATE_PART('year',  c.cohort_month)) * 12 +
        (DATE_PART('month', DATE_TRUNC('month', o.placed_at)) -
         DATE_PART('month', c.cohort_month)) AS months_since_first
    FROM cohorts c
    JOIN orders o ON o.customer_id = c.customer_id
    WHERE o.order_status NOT IN ('cancelled', 'pending')
)
SELECT
    cohort_month,
    months_since_first          AS month_number,
    COUNT(DISTINCT customer_id) AS active_customers
FROM cohort_orders
GROUP BY cohort_month, months_since_first
ORDER BY cohort_month, months_since_first;
