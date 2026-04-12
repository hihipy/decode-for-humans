/**
 * test_javascript.js
 *
 * A client-side inventory management module for a retail web application.
 *
 * Handles fetching product data from a REST API, local caching with
 * expiry, search and filtering, cart management, and checkout submission.
 * Uses modern ES2022+ syntax throughout.
 */

"use strict";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API_BASE_URL      = "https://api.example-store.com/v2";
const CACHE_TTL_MS      = 5 * 60 * 1000;   // 5 minutes
const MAX_CART_ITEMS    = 50;
const LOW_STOCK_CUTOFF  = 5;
const RETRY_ATTEMPTS    = 3;
const RETRY_DELAY_MS    = 1000;

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

/**
 * Pause execution for the given number of milliseconds.
 * @param {number} ms - Duration to wait.
 * @returns {Promise<void>}
 */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Deep-clone a plain object using structured clone when available.
 * @template T
 * @param {T} obj - Object to clone.
 * @returns {T}
 */
const deepClone = (obj) =>
  typeof structuredClone === "function"
    ? structuredClone(obj)
    : JSON.parse(JSON.stringify(obj));

/**
 * Format a number as a currency string.
 * @param {number} amount - Numeric amount in the base currency unit.
 * @param {string} [currency="USD"] - ISO 4217 currency code.
 * @returns {string} Formatted string e.g. "$12.99".
 */
const formatCurrency = (amount, currency = "USD") =>
  new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount);

// ---------------------------------------------------------------------------
// In-memory cache
// ---------------------------------------------------------------------------

class ExpiringCache {
  /**
   * A simple key-value store where entries expire after a fixed TTL.
   * @param {number} ttlMs - Time-to-live in milliseconds for each entry.
   */
  constructor(ttlMs = CACHE_TTL_MS) {
    /** @type {Map<string, { value: any, expiresAt: number }>} */
    this._store  = new Map();
    this._ttlMs  = ttlMs;
  }

  /**
   * Read a cached value, returning undefined if missing or expired.
   * @param {string} key
   * @returns {any | undefined}
   */
  get(key) {
    const entry = this._store.get(key);
    if (!entry) return undefined;
    if (Date.now() > entry.expiresAt) {
      this._store.delete(key);
      return undefined;
    }
    return entry.value;
  }

  /**
   * Store a value with an expiry timestamp.
   * @param {string} key
   * @param {any} value
   */
  set(key, value) {
    this._store.set(key, {
      value,
      expiresAt: Date.now() + this._ttlMs,
    });
  }

  /** Remove all entries from the cache. */
  clear() {
    this._store.clear();
  }

  /** @returns {number} The number of (possibly stale) entries in the store. */
  get size() {
    return this._store.size;
  }
}

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------

class InventoryAPIClient {
  /**
   * Wraps fetch() with automatic retries, error normalisation, and caching.
   * @param {string} baseUrl - Root URL for all API requests.
   * @param {ExpiringCache} cache - Shared cache instance.
   */
  constructor(baseUrl, cache) {
    this._baseUrl = baseUrl;
    this._cache   = cache;
  }

  /**
   * Perform a GET request with retry logic and response caching.
   * @param {string} endpoint - Path appended to baseUrl (e.g. "/products").
   * @param {Record<string, string>} [params={}] - Query string parameters.
   * @returns {Promise<any>} Parsed JSON response body.
   * @throws {Error} After all retry attempts are exhausted.
   */
  async get(endpoint, params = {}) {
    const url       = this._buildUrl(endpoint, params);
    const cacheKey  = url.toString();
    const cached    = this._cache.get(cacheKey);

    if (cached !== undefined) {
      return deepClone(cached);
    }

    let lastError;
    for (let attempt = 1; attempt <= RETRY_ATTEMPTS; attempt++) {
      try {
        const response = await fetch(url, {
          method: "GET",
          headers: { Accept: "application/json" },
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();
        this._cache.set(cacheKey, data);
        return deepClone(data);

      } catch (err) {
        lastError = err;
        if (attempt < RETRY_ATTEMPTS) {
          await sleep(RETRY_DELAY_MS * attempt);
        }
      }
    }

    throw new Error(
      `API request failed after ${RETRY_ATTEMPTS} attempts: ${lastError.message}`
    );
  }

  /**
   * Submit a POST request (not cached).
   * @param {string} endpoint
   * @param {object} body - Request payload, serialised to JSON.
   * @returns {Promise<any>} Parsed JSON response body.
   */
  async post(endpoint, body) {
    const response = await fetch(`${this._baseUrl}${endpoint}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`POST ${endpoint} failed (${response.status}): ${detail}`);
    }

    return response.json();
  }

  /**
   * Build a fully qualified URL from an endpoint and query params.
   * @param {string} endpoint
   * @param {Record<string, string>} params
   * @returns {URL}
   */
  _buildUrl(endpoint, params) {
    const url = new URL(`${this._baseUrl}${endpoint}`);
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
    return url;
  }
}

// ---------------------------------------------------------------------------
// Product model
// ---------------------------------------------------------------------------

class Product {
  /**
   * @param {object} raw - Raw API response object for a single product.
   */
  constructor({ id, name, sku, price, stock, category, tags = [] }) {
    this.id       = id;
    this.name     = name;
    this.sku      = sku;
    this.price    = price;
    this.stock    = stock;
    this.category = category;
    this.tags     = tags;
  }

  /** @returns {boolean} True if fewer than LOW_STOCK_CUTOFF units remain. */
  get isLowStock() {
    return this.stock > 0 && this.stock < LOW_STOCK_CUTOFF;
  }

  /** @returns {boolean} True if no units are available. */
  get isOutOfStock() {
    return this.stock <= 0;
  }

  /** @returns {string} Localised currency string for the price. */
  get formattedPrice() {
    return formatCurrency(this.price);
  }
}

// ---------------------------------------------------------------------------
// Cart
// ---------------------------------------------------------------------------

class ShoppingCart {
  constructor() {
    /** @type {Map<string, { product: Product, quantity: number }>} */
    this._items = new Map();
  }

  /**
   * Add a product to the cart or increase its quantity.
   * @param {Product} product - The product to add.
   * @param {number} [quantity=1] - Number of units to add.
   * @throws {Error} If cart is full or requested quantity exceeds stock.
   */
  add(product, quantity = 1) {
    if (this._items.size >= MAX_CART_ITEMS && !this._items.has(product.id)) {
      throw new Error(`Cart is full (max ${MAX_CART_ITEMS} distinct items).`);
    }

    const existing  = this._items.get(product.id);
    const newQty    = (existing?.quantity ?? 0) + quantity;

    if (newQty > product.stock) {
      throw new Error(
        `Cannot add ${quantity} × "${product.name}": only ${product.stock} in stock.`
      );
    }

    this._items.set(product.id, { product, quantity: newQty });
  }

  /**
   * Remove all units of a product from the cart.
   * @param {string} productId
   */
  remove(productId) {
    this._items.delete(productId);
  }

  /**
   * Update the quantity of a product already in the cart.
   * @param {string} productId
   * @param {number} quantity - New desired quantity (0 removes the item).
   */
  updateQuantity(productId, quantity) {
    if (quantity <= 0) {
      this.remove(productId);
      return;
    }
    const entry = this._items.get(productId);
    if (!entry) return;
    this._items.set(productId, { ...entry, quantity });
  }

  /** Remove everything from the cart. */
  clear() {
    this._items.clear();
  }

  /** @returns {number} Total number of individual units across all items. */
  get totalUnits() {
    return [...this._items.values()].reduce((sum, { quantity }) => sum + quantity, 0);
  }

  /** @returns {number} Raw numeric total before tax. */
  get subtotal() {
    return [...this._items.values()].reduce(
      (sum, { product, quantity }) => sum + product.price * quantity,
      0
    );
  }

  /** @returns {string} Formatted subtotal string. */
  get formattedSubtotal() {
    return formatCurrency(this.subtotal);
  }

  /**
   * Return a plain-object snapshot of the cart suitable for an API payload.
   * @returns {{ items: Array<{ sku: string, quantity: number }>, total: number }}
   */
  toOrderPayload() {
    return {
      items: [...this._items.values()].map(({ product, quantity }) => ({
        sku: product.sku,
        quantity,
      })),
      total: this.subtotal,
    };
  }
}

// ---------------------------------------------------------------------------
// Inventory store (facade)
// ---------------------------------------------------------------------------

class InventoryStore {
  /**
   * High-level facade used by UI code to browse products and manage a cart.
   * @param {InventoryAPIClient} client
   */
  constructor(client) {
    this._client  = client;
    this._cart    = new ShoppingCart();
    /** @type {Product[]} */
    this._catalog = [];
  }

  /**
   * Fetch the full product catalogue from the API and populate _catalog.
   * @returns {Promise<Product[]>}
   */
  async loadCatalog() {
    const raw = await this._client.get("/products");
    this._catalog = raw.map((item) => new Product(item));
    return this._catalog;
  }

  /**
   * Filter the in-memory catalogue by keyword and optional category.
   * @param {string} query - Substring matched against name, SKU, and tags.
   * @param {string | null} [category=null] - Exact category match, or null.
   * @returns {Product[]}
   */
  search(query, category = null) {
    const term = query.toLowerCase().trim();
    return this._catalog.filter((p) => {
      const matchesQuery =
        !term ||
        p.name.toLowerCase().includes(term) ||
        p.sku.toLowerCase().includes(term) ||
        p.tags.some((t) => t.toLowerCase().includes(term));

      const matchesCategory = !category || p.category === category;
      return matchesQuery && matchesCategory;
    });
  }

  /**
   * Return all products that are running low or out of stock.
   * @returns {Product[]}
   */
  getLowStockProducts() {
    return this._catalog.filter((p) => p.isLowStock || p.isOutOfStock);
  }

  /** @returns {ShoppingCart} The active shopping cart. */
  get cart() {
    return this._cart;
  }

  /**
   * Submit the current cart as an order and clear it on success.
   * @returns {Promise<{ orderId: string, estimatedDelivery: string }>}
   */
  async checkout() {
    if (this._cart.totalUnits === 0) {
      throw new Error("Cannot checkout an empty cart.");
    }

    const payload = this._cart.toOrderPayload();
    const result  = await this._client.post("/orders", payload);
    this._cart.clear();
    return result;
  }
}

// ---------------------------------------------------------------------------
// Bootstrap / demo
// ---------------------------------------------------------------------------

async function bootstrap() {
  const cache  = new ExpiringCache(CACHE_TTL_MS);
  const client = new InventoryAPIClient(API_BASE_URL, cache);
  const store  = new InventoryStore(client);

  try {
    console.log("Loading catalogue...");
    const products = await store.loadCatalog();
    console.log(`Loaded ${products.length} products.`);

    const results = store.search("widget", "hardware");
    console.log(`Search returned ${results.length} result(s).`);

    const [first] = results;
    if (first && !first.isOutOfStock) {
      store.cart.add(first, 2);
      console.log(`Cart subtotal: ${store.cart.formattedSubtotal}`);
    }

    const lowStock = store.getLowStockProducts();
    if (lowStock.length > 0) {
      console.warn(`Low-stock alert: ${lowStock.map((p) => p.name).join(", ")}`);
    }

  } catch (err) {
    console.error("Inventory error:", err.message);
  }
}

bootstrap();
