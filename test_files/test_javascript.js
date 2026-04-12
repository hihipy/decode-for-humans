/**
 * test_javascript.js
 *
 * Holistic JavaScript showcase — real-time event ticketing platform.
 *
 * Covers: ES2022+ classes, private fields, static blocks, closures,
 * promises, async/await, generators, iterators, Proxy, WeakMap, Symbol,
 * destructuring, spread/rest, optional chaining, nullish coalescing,
 * tagged template literals, Map/Set, error subclassing, module patterns,
 * prototype chain, getter/setter, Reflect, event emitter pattern,
 * memoisation, debounce/throttle, pipeline operator pattern, and more.
 */

"use strict";

// ---------------------------------------------------------------------------
// Constants and symbols
// ---------------------------------------------------------------------------

const APP_VERSION       = "3.1.0";
const MAX_TICKETS       = 500;
const BOOKING_FEE_PCT   = 0.05;
const REFUND_WINDOW_MS  = 48 * 60 * 60 * 1000;   // 48 hours
const SOLD_OUT_SYMBOL   = Symbol("SOLD_OUT");
const CANCELLED_SYMBOL  = Symbol("CANCELLED");

// ---------------------------------------------------------------------------
// Utility — pure functions
// ---------------------------------------------------------------------------

const formatCurrency = (amount, currency = "GBP") =>
  new Intl.NumberFormat("en-GB", { style: "currency", currency }).format(amount);

const formatDate = (date) =>
  new Intl.DateTimeFormat("en-GB", {
    dateStyle: "long", timeStyle: "short",
  }).format(date);

const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const generateId = (() => {
  let counter = 1000;
  return (prefix = "ID") => `${prefix}-${(++counter).toString(36).toUpperCase()}`;
})();

// Memoisation
const memoize = (fn) => {
  const cache = new Map();
  return (...args) => {
    const key = JSON.stringify(args);
    if (!cache.has(key)) cache.set(key, fn(...args));
    return cache.get(key);
  };
};

// Debounce
const debounce = (fn, ms) => {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
};

// Tagged template — sanitise HTML
const safe = (strings, ...values) =>
  strings.reduce((acc, str, i) => {
    const val = values[i - 1] ?? "";
    return acc + String(val).replace(/[<>&"]/g, (c) =>
      ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c])
    ) + str;
  });

// ---------------------------------------------------------------------------
// Custom errors
// ---------------------------------------------------------------------------

class AppError extends Error {
  constructor(message, code = "ERR_GENERIC") {
    super(message);
    this.name = this.constructor.name;
    this.code = code;
  }
}

class SoldOutError    extends AppError { constructor(e) { super(`Event "${e}" is sold out.`, "ERR_SOLD_OUT"); } }
class BookingError    extends AppError {}
class RefundError     extends AppError {}
class ValidationError extends AppError { constructor(f, m) { super(`${f}: ${m}`, "ERR_VALIDATION"); } }

// ---------------------------------------------------------------------------
// Event emitter (mixin pattern)
// ---------------------------------------------------------------------------

class EventEmitter {
  #listeners = new Map();

  on(event, handler) {
    if (!this.#listeners.has(event)) this.#listeners.set(event, new Set());
    this.#listeners.get(event).add(handler);
    return () => this.off(event, handler);
  }

  off(event, handler) {
    this.#listeners.get(event)?.delete(handler);
  }

  emit(event, ...args) {
    this.#listeners.get(event)?.forEach((h) => h(...args));
  }

  once(event, handler) {
    const wrapper = (...args) => { handler(...args); this.off(event, wrapper); };
    return this.on(event, wrapper);
  }
}

// ---------------------------------------------------------------------------
// Venue
// ---------------------------------------------------------------------------

class Venue {
  #capacity;
  #facilities;

  constructor({ name, address, capacity, facilities = [] }) {
    this.name        = name;
    this.address     = address;
    this.#capacity   = capacity;
    this.#facilities = new Set(facilities);
  }

  get capacity()     { return this.#capacity; }
  get facilities()   { return [...this.#facilities]; }

  hasFacility(name) { return this.#facilities.has(name); }

  toString() {
    return `${this.name} (cap. ${this.#capacity.toLocaleString()})`;
  }
}

// ---------------------------------------------------------------------------
// Ticket types
// ---------------------------------------------------------------------------

class TicketType {
  static #registry = new Map();

  static {
    // Static initialisation block — register default types
    [
      { id: "GA",  label: "General Admission", multiplier: 1.0 },
      { id: "VIP", label: "VIP",               multiplier: 2.5 },
      { id: "EAR", label: "Early Bird",        multiplier: 0.8 },
    ].forEach((t) => TicketType.#registry.set(t.id, new TicketType(t)));
  }

  static get(id) { return TicketType.#registry.get(id) ?? null; }
  static all()   { return [...TicketType.#registry.values()]; }

  #id; #label; #multiplier;

  constructor({ id, label, multiplier }) {
    this.#id         = id;
    this.#label      = label;
    this.#multiplier = multiplier;
  }

  get id()         { return this.#id; }
  get label()      { return this.#label; }
  get multiplier() { return this.#multiplier; }

  priceFor(basePrice) {
    return Math.round(basePrice * this.#multiplier * 100) / 100;
  }

  [Symbol.toPrimitive](hint) {
    return hint === "number" ? this.#multiplier : this.#label;
  }
}

// ---------------------------------------------------------------------------
// Event
// ---------------------------------------------------------------------------

class Event extends EventEmitter {
  #status        = "on_sale";
  #soldTickets   = new Map();       // bookingId → Booking
  #waitlist      = [];
  #priceHistory  = [];

  constructor({ title, venue, date, basePrice, maxTickets = MAX_TICKETS }) {
    super();
    this.id         = generateId("EVT");
    this.title      = title;
    this.venue      = venue instanceof Venue ? venue : new Venue(venue);
    this.date       = date instanceof Date ? date : new Date(date);
    this.basePrice  = basePrice;
    this.maxTickets = clamp(maxTickets, 1, this.venue.capacity);
    this.#priceHistory.push({ price: basePrice, at: new Date() });
  }

  get status()       { return this.#status; }
  get ticketsSold()  { return this.#soldTickets.size; }
  get ticketsLeft()  { return this.maxTickets - this.ticketsSold; }
  get isSoldOut()    { return this.ticketsLeft <= 0; }
  get waitlistSize() { return this.#waitlist.length; }

  updateBasePrice(newPrice) {
    if (newPrice <= 0) throw new ValidationError("basePrice", "must be positive");
    const old = this.basePrice;
    this.basePrice = newPrice;
    this.#priceHistory.push({ price: newPrice, at: new Date() });
    this.emit("priceChange", { event: this, old, new: newPrice });
  }

  _recordBooking(booking) {
    this.#soldTickets.set(booking.id, booking);
    if (this.isSoldOut) {
      this.#status = SOLD_OUT_SYMBOL.toString();
      this.emit("soldOut", this);
    }
    this.emit("booked", booking);
  }

  _cancelBooking(bookingId) {
    const booking = this.#soldTickets.get(bookingId);
    if (!booking) return false;
    this.#soldTickets.delete(bookingId);
    this.emit("cancelled", booking);
    this._processWaitlist();
    return true;
  }

  _processWaitlist() {
    if (this.#waitlist.length === 0 || this.isSoldOut) return;
    const next = this.#waitlist.shift();
    this.emit("waitlistReady", next);
  }

  joinWaitlist(customer) {
    this.#waitlist.push(customer);
    return this.#waitlist.length;
  }

  *[Symbol.iterator]() {
    yield* this.#soldTickets.values();
  }

  get priceHistory() { return [...this.#priceHistory]; }

  toJSON() {
    return {
      id:          this.id,
      title:       this.title,
      venue:       this.venue.name,
      date:        this.date.toISOString(),
      basePrice:   this.basePrice,
      ticketsSold: this.ticketsSold,
      ticketsLeft: this.ticketsLeft,
      status:      this.status,
    };
  }
}

// ---------------------------------------------------------------------------
// Booking
// ---------------------------------------------------------------------------

class Booking {
  #paid      = false;
  #refunded  = false;
  #createdAt = new Date();

  constructor({ event, customer, ticketType, quantity = 1 }) {
    this.id         = generateId("BKG");
    this.event      = event;
    this.customer   = customer;
    this.ticketType = ticketType instanceof TicketType
      ? ticketType
      : TicketType.get(ticketType) ?? TicketType.get("GA");
    this.quantity   = quantity;
    this.unitPrice  = this.ticketType.priceFor(event.basePrice);
  }

  get createdAt()    { return this.#createdAt; }
  get isPaid()       { return this.#paid; }
  get isRefunded()   { return this.#refunded; }
  get subtotal()     { return Math.round(this.unitPrice * this.quantity * 100) / 100; }
  get bookingFee()   { return Math.round(this.subtotal * BOOKING_FEE_PCT * 100) / 100; }
  get total()        { return Math.round((this.subtotal + this.bookingFee) * 100) / 100; }

  get isRefundEligible() {
    return this.#paid && !this.#refunded &&
      (Date.now() - this.#createdAt.getTime()) < REFUND_WINDOW_MS;
  }

  markPaid() {
    if (this.#paid) throw new BookingError("Already paid.", "ERR_ALREADY_PAID");
    this.#paid = true;
    return this;
  }

  markRefunded() {
    if (!this.isRefundEligible) throw new RefundError("Refund window closed.", "ERR_REFUND_WINDOW");
    this.#refunded = true;
    return this;
  }

  toReceipt() {
    const lines = [
      `BOOKING CONFIRMATION`,
      `${"─".repeat(40)}`,
      `Ref       : ${this.id}`,
      `Event     : ${this.event.title}`,
      `Date      : ${formatDate(this.event.date)}`,
      `Venue     : ${this.event.venue}`,
      `Ticket    : ${this.ticketType.label} × ${this.quantity}`,
      `Unit price: ${formatCurrency(this.unitPrice)}`,
      `Subtotal  : ${formatCurrency(this.subtotal)}`,
      `Booking   : ${formatCurrency(this.bookingFee)}`,
      `${"═".repeat(40)}`,
      `TOTAL     : ${formatCurrency(this.total)}`,
      `Status    : ${this.isPaid ? "PAID" : "AWAITING PAYMENT"}`,
    ];
    return lines.join("\n");
  }
}

// ---------------------------------------------------------------------------
// Booking engine — Proxy-guarded inventory
// ---------------------------------------------------------------------------

class BookingEngine {
  #events      = new Map();
  #bookings    = new Map();
  #weakCustomer = new WeakMap();

  constructor() {
    // Proxy wraps the engine so unknown method calls are caught
    return new Proxy(this, {
      get(target, prop) {
        if (!(prop in target) && typeof prop === "string") {
          throw new AppError(`Unknown method: BookingEngine.${prop}`, "ERR_API");
        }
        return Reflect.get(target, prop);
      },
    });
  }

  registerEvent(event) {
    if (!(event instanceof Event)) throw new ValidationError("event", "must be an Event instance");
    this.#events.set(event.id, event);
    return event;
  }

  getEvent(id) {
    return this.#events.get(id) ?? null;
  }

  book({ eventId, customer, ticketTypeId = "GA", quantity = 1 }) {
    const event = this.#events.get(eventId);
    if (!event) throw new BookingError(`Event ${eventId} not found.`, "ERR_NOT_FOUND");
    if (event.isSoldOut) throw new SoldOutError(event.title);
    if (quantity > event.ticketsLeft) {
      throw new BookingError(
        `Only ${event.ticketsLeft} ticket(s) left.`, "ERR_PARTIAL_STOCK"
      );
    }

    const ticketType = TicketType.get(ticketTypeId);
    const booking    = new Booking({ event, customer, ticketType, quantity });

    booking.markPaid();
    event._recordBooking(booking);
    this.#bookings.set(booking.id, booking);
    this.#weakCustomer.set(customer, booking.id);

    return booking;
  }

  cancel(bookingId) {
    const booking = this.#bookings.get(bookingId);
    if (!booking) throw new BookingError(`Booking ${bookingId} not found.`);
    booking.markRefunded();
    booking.event._cancelBooking(bookingId);
    return true;
  }

  // Async batch import using a generator
  async *importEvents(rawList) {
    for (const raw of rawList) {
      await sleep(10);
      const event = new Event(raw);
      this.registerEvent(event);
      yield event;
    }
  }

  // Summarise all bookings using array destructuring and reduce
  summary() {
    const all = [...this.#bookings.values()];
    const { total, revenue } = all.reduce(
      ({ total, revenue }, b) => ({
        total:   total + 1,
        revenue: revenue + (b.isPaid ? b.total : 0),
      }),
      { total: 0, revenue: 0 }
    );
    return { bookingCount: total, totalRevenue: revenue };
  }
}

// ---------------------------------------------------------------------------
// Async report generator
// ---------------------------------------------------------------------------

async function* generateDailyReport(engine, days = 7) {
  for (let i = 0; i < days; i++) {
    await sleep(5);
    const { bookingCount, totalRevenue } = engine.summary();
    yield {
      day:          i + 1,
      bookingCount,
      totalRevenue: formatCurrency(totalRevenue),
    };
  }
}

// ---------------------------------------------------------------------------
// Pipeline helper (simulated |> operator)
// ---------------------------------------------------------------------------

const pipe = (...fns) => (x) => fns.reduce((v, f) => f(v), x);

const normaliseTitle  = (s) => s.trim().toLowerCase().replace(/\s+/g, "-");
const addTimestamp    = (s) => `${s}-${Date.now()}`;
const toUpperSlug     = (s) => s.toUpperCase();

const makeSlug = pipe(normaliseTitle, addTimestamp, toUpperSlug);

// ---------------------------------------------------------------------------
// Bootstrap / demo
// ---------------------------------------------------------------------------

async function main() {
  console.log(`Ticketing Platform v${APP_VERSION}\n`);

  const engine = new BookingEngine();

  // Batch import events
  const rawEvents = [
    {
      title:      "Jazz Under the Stars",
      venue:      { name: "Roundhouse",    address: "Chalk Farm Rd", capacity: 3300 },
      date:       new Date(Date.now() + 30 * 864e5),
      basePrice:  45,
      maxTickets: 200,
    },
    {
      title:      "Tech Summit 2025",
      venue:      { name: "ExCeL London",  address: "Royal Docks",  capacity: 5000 },
      date:       new Date(Date.now() + 60 * 864e5),
      basePrice:  299,
      maxTickets: 150,
    },
  ];

  const importedEvents = [];
  for await (const event of engine.importEvents(rawEvents)) {
    console.log(`Registered: ${event.title} [${event.id}]`);
    importedEvents.push(event);
  }

  const [jazzEvent, techEvent] = importedEvents;

  // Subscribe to events
  const unsubscribe = jazzEvent.on("booked", (booking) => {
    console.log(`Booked: ${booking.id} — ${formatCurrency(booking.total)}`);
  });

  jazzEvent.once("soldOut", (evt) => {
    console.warn(`SOLD OUT: ${evt.title}`);
  });

  // Make bookings
  const alice = { name: "Alice Chen",  email: "alice@example.com" };
  const bob   = { name: "Bob Okafor",  email: "bob@example.com" };

  const b1 = engine.book({ eventId: jazzEvent.id, customer: alice, ticketTypeId: "VIP",  quantity: 2 });
  const b2 = engine.book({ eventId: jazzEvent.id, customer: bob,   ticketTypeId: "GA",   quantity: 3 });
  const b3 = engine.book({ eventId: techEvent.id, customer: alice, ticketTypeId: "EAR",  quantity: 1 });

  console.log("\n" + b1.toReceipt());

  // Price history after update
  jazzEvent.updateBasePrice(55);
  console.log("\nPrice history:", jazzEvent.priceHistory.map(h =>
    `${formatCurrency(h.price)} @ ${h.at.toISOString()}`
  ));

  // Iterate over all bookings on an event
  console.log("\nJazz bookings:");
  for (const booking of jazzEvent) {
    console.log(`  ${booking.id} — ${booking.customer.name} — ${formatCurrency(booking.total)}`);
  }

  // Summary
  const { bookingCount, totalRevenue } = engine.summary();
  console.log(`\nSummary: ${bookingCount} bookings, ${formatCurrency(totalRevenue)} revenue`);

  // Slug pipeline
  console.log("\nEvent slug:", makeSlug(jazzEvent.title));

  // Memoized price computation
  const memoPrice = memoize((base, mult) => base * mult);
  console.log("Memoized:", memoPrice(45, 2.5), memoPrice(45, 2.5));

  // Debounced search (demo only)
  const debouncedSearch = debounce((q) => console.log("Search:", q), 300);
  debouncedSearch("jazz");

  // Cancel booking
  try {
    engine.cancel(b2.id);
    console.log(`\nCancelled ${b2.id} OK`);
  } catch (err) {
    console.error(err.message);
  }

  // Async report
  console.log("\nDaily report preview:");
  let count = 0;
  for await (const row of generateDailyReport(engine, 3)) {
    console.log(`  Day ${row.day}: ${row.bookingCount} bookings, ${row.totalRevenue}`);
    if (++count >= 3) break;
  }

  // Safe tagged template
  const userInput = "<script>alert('xss')</script>";
  console.log("\nSanitised:", safe`Hello ${userInput}!`);

  // Cleanup
  unsubscribe();
}

main().catch(console.error);
