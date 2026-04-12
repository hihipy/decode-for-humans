/**
 * test_typescript.ts
 *
 * Holistic TypeScript showcase — multi-tenant SaaS billing system.
 *
 * Covers: interfaces, generics, utility types, conditional types,
 * mapped types, template literal types, decorators, enums, namespaces,
 * discriminated unions, intersection types, type guards, assertion functions,
 * abstract classes, readonly, const assertions, satisfies, infer,
 * module augmentation, ambient declarations, branded types, and more.
 */

// ---------------------------------------------------------------------------
// Branded / nominal types
// ---------------------------------------------------------------------------

declare const _brand: unique symbol;
type Brand<T, B> = T & { readonly [_brand]: B };

type UserId    = Brand<string, "UserId">;
type TenantId  = Brand<string, "TenantId">;
type InvoiceId = Brand<string, "InvoiceId">;
type Cents     = Brand<number, "Cents">;

const toUserId    = (id: string): UserId    => id as UserId;
const toTenantId  = (id: string): TenantId  => id as TenantId;
const toInvoiceId = (id: string): InvoiceId => id as InvoiceId;
const toCents     = (n: number):  Cents     => Math.round(n) as Cents;

// ---------------------------------------------------------------------------
// Const assertion and satisfies
// ---------------------------------------------------------------------------

const PLANS = {
  free:       { name: "Free",       monthlyPrice: toCents(0),     seats: 1,   features: ["core"]            },
  starter:    { name: "Starter",    monthlyPrice: toCents(1900),  seats: 5,   features: ["core", "api"]     },
  pro:        { name: "Pro",        monthlyPrice: toCents(4900),  seats: 20,  features: ["core", "api", "sso"] },
  enterprise: { name: "Enterprise", monthlyPrice: toCents(19900), seats: 999, features: ["core", "api", "sso", "audit"] },
} as const;

type PlanId = keyof typeof PLANS;
type Plan   = (typeof PLANS)[PlanId];

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

enum BillingCycle {
  Monthly  = "monthly",
  Annually = "annually",
}

enum InvoiceStatus {
  Draft   = "draft",
  Open    = "open",
  Paid    = "paid",
  Void    = "void",
  Overdue = "overdue",
}

// ---------------------------------------------------------------------------
// Template literal types
// ---------------------------------------------------------------------------

type HttpMethod   = "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
type ApiVersion   = "v1" | "v2";
type ApiRoute     = `/${ApiVersion}/${string}`;
type EventName    = `on${Capitalize<string>}`;
type ErrorCode    = `ERR_${"AUTH" | "BILLING" | "RATE_LIMIT" | "VALIDATION"}`;

// ---------------------------------------------------------------------------
// Utility types composition
// ---------------------------------------------------------------------------

interface Timestamped {
  readonly createdAt: Date;
  readonly updatedAt: Date;
}

interface SoftDeletable {
  readonly deletedAt: Date | null;
}

type Entity = Timestamped & SoftDeletable;

type CreateInput<T extends Entity> = Omit<T, keyof Entity | "id">;
type UpdateInput<T>                = Partial<Omit<T, "id" | "createdAt">>;
type PublicView<T>                 = Omit<T, "deletedAt" | "internalNotes">;

// ---------------------------------------------------------------------------
// Interfaces
// ---------------------------------------------------------------------------

interface Identifiable {
  readonly id: string;
}

interface Address {
  readonly line1:    string;
  readonly city:     string;
  readonly country:  string;
  readonly postcode: string;
}

interface Tenant extends Identifiable, Entity {
  readonly tenantId:      TenantId;
  name:                   string;
  planId:                 PlanId;
  billingCycle:           BillingCycle;
  billingEmail:           string;
  address:                Address;
  seatCount:              number;
  internalNotes?:         string;
}

interface User extends Identifiable, Entity {
  readonly userId:    UserId;
  readonly tenantId:  TenantId;
  email:              string;
  displayName:        string;
  role:               UserRole;
  lastLoginAt:        Date | null;
}

interface Invoice extends Identifiable, Entity {
  readonly invoiceId:  InvoiceId;
  readonly tenantId:   TenantId;
  status:              InvoiceStatus;
  lineItems:           readonly LineItem[];
  subtotalCents:       Cents;
  taxCents:            Cents;
  totalCents:          Cents;
  dueDate:             Date;
  paidAt:              Date | null;
}

interface LineItem {
  description: string;
  quantity:    number;
  unitCents:   Cents;
  totalCents:  Cents;
}

// ---------------------------------------------------------------------------
// Discriminated unions
// ---------------------------------------------------------------------------

type UserRole =
  | { kind: "owner";  tenantId: TenantId }
  | { kind: "admin";  tenantId: TenantId }
  | { kind: "member"; tenantId: TenantId }
  | { kind: "guest"                      };

type PaymentMethod =
  | { type: "card";   last4: string;  brand: "visa" | "mastercard" | "amex" }
  | { type: "bank";   accountLast4: string; routingNumber: string            }
  | { type: "crypto"; wallet: string; coin: "BTC" | "ETH"                   };

type BillingEvent =
  | { event: "invoice.created"; invoice: Invoice                        }
  | { event: "invoice.paid";    invoice: Invoice; paidAt: Date          }
  | { event: "plan.upgraded";   tenantId: TenantId; newPlan: PlanId     }
  | { event: "seat.added";      tenantId: TenantId; userId: UserId      };

// ---------------------------------------------------------------------------
// Conditional and mapped types
// ---------------------------------------------------------------------------

type DeepReadonly<T> = T extends (infer U)[]
  ? ReadonlyArray<DeepReadonly<U>>
  : T extends object
  ? { readonly [K in keyof T]: DeepReadonly<T[K]> }
  : T;

type NonNullableFields<T> = { [K in keyof T]: NonNullable<T[K]> };

type OptionalFields<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;

// Infer the resolved value of a promise
type Awaited<T> = T extends Promise<infer U> ? Awaited<U> : T;

// Extract keys whose value extends a type
type KeysOfType<T, V> = { [K in keyof T]: T[K] extends V ? K : never }[keyof T];

// ---------------------------------------------------------------------------
// Type guards and assertion functions
// ---------------------------------------------------------------------------

function isInvoice(value: unknown): value is Invoice {
  return (
    typeof value === "object" &&
    value !== null &&
    "invoiceId" in value &&
    "status" in value
  );
}

function assertDefined<T>(value: T, name: string): asserts value is NonNullable<T> {
  if (value === null || value === undefined) {
    throw new Error(`Expected ${name} to be defined, got ${value}.`);
  }
}

function isOwnerOrAdmin(role: UserRole): role is Extract<UserRole, { kind: "owner" | "admin" }> {
  return role.kind === "owner" || role.kind === "admin";
}

// ---------------------------------------------------------------------------
// Generic Result type (Railway-oriented)
// ---------------------------------------------------------------------------

type Result<T, E = Error> =
  | { ok: true;  value: T }
  | { ok: false; error: E };

const Ok  = <T>(value: T): Result<T, never>    => ({ ok: true,  value });
const Err = <E>(error: E): Result<never, E>     => ({ ok: false, error });

function mapResult<T, U, E>(
  result: Result<T, E>,
  fn: (value: T) => U,
): Result<U, E> {
  return result.ok ? Ok(fn(result.value)) : result;
}

// ---------------------------------------------------------------------------
// Generic repository interface
// ---------------------------------------------------------------------------

interface Repository<T extends Identifiable> {
  findById(id: string): Promise<T | null>;
  findMany(filter: Partial<T>): Promise<T[]>;
  create(input: Omit<T, "id" | keyof Entity>): Promise<T>;
  update(id: string, patch: UpdateInput<T>): Promise<T | null>;
  delete(id: string): Promise<boolean>;
}

// ---------------------------------------------------------------------------
// Abstract base service
// ---------------------------------------------------------------------------

abstract class BaseService<T extends Identifiable> {
  protected abstract readonly repo: Repository<T>;

  async getOrThrow(id: string): Promise<T> {
    const entity = await this.repo.findById(id);
    if (!entity) throw new Error(`Entity ${id} not found.`);
    return entity;
  }

  async exists(id: string): Promise<boolean> {
    return (await this.repo.findById(id)) !== null;
  }
}

// ---------------------------------------------------------------------------
// In-memory repository implementation
// ---------------------------------------------------------------------------

class InMemoryRepo<T extends Identifiable & Entity> implements Repository<T> {
  protected store = new Map<string, T>();
  private   idSeq = 0;

  private now(): Date { return new Date(); }

  async findById(id: string): Promise<T | null> {
    return this.store.get(id) ?? null;
  }

  async findMany(filter: Partial<T>): Promise<T[]> {
    return [...this.store.values()].filter((item) =>
      Object.entries(filter).every(([k, v]) => (item as any)[k] === v)
    );
  }

  async create(input: Omit<T, "id" | keyof Entity>): Promise<T> {
    const id = `${++this.idSeq}`;
    const now = this.now();
    const entity = {
      ...input,
      id,
      createdAt: now,
      updatedAt: now,
      deletedAt: null,
    } as unknown as T;
    this.store.set(id, entity);
    return entity;
  }

  async update(id: string, patch: UpdateInput<T>): Promise<T | null> {
    const entity = this.store.get(id);
    if (!entity) return null;
    const updated = { ...entity, ...patch, updatedAt: this.now() } as T;
    this.store.set(id, updated);
    return updated;
  }

  async delete(id: string): Promise<boolean> {
    return this.store.delete(id);
  }
}

// ---------------------------------------------------------------------------
// Billing service
// ---------------------------------------------------------------------------

interface BillingServiceConfig {
  taxRatePct:       number;
  gracePeriodDays:  number;
}

class BillingService extends BaseService<Invoice> {
  protected readonly repo: InMemoryRepo<Invoice & Entity>;
  private readonly config: Readonly<BillingServiceConfig>;
  private readonly eventHandlers = new Map<string, Array<(e: BillingEvent) => void>>();

  constructor(config: BillingServiceConfig) {
    super();
    this.repo   = new InMemoryRepo();
    this.config = Object.freeze({ ...config });
  }

  on<E extends BillingEvent["event"]>(
    event: E,
    handler: (e: Extract<BillingEvent, { event: E }>) => void,
  ): void {
    if (!this.eventHandlers.has(event)) this.eventHandlers.set(event, []);
    this.eventHandlers.get(event)!.push(handler as any);
  }

  private emit(event: BillingEvent): void {
    this.eventHandlers.get(event.event)?.forEach((h) => h(event as any));
  }

  createInvoice(
    tenantId:  TenantId,
    lineItems: LineItem[],
    dueDate:   Date,
  ): Result<Invoice, string> {
    if (lineItems.length === 0) return Err("Invoice must have at least one line item.");

    const subtotalCents = toCents(lineItems.reduce((s, l) => s + l.totalCents, 0));
    const taxCents      = toCents(Math.round(subtotalCents * this.config.taxRatePct));
    const totalCents    = toCents(subtotalCents + taxCents);

    const invoice: Omit<Invoice, "id" | keyof Entity> = {
      invoiceId:     toInvoiceId(`inv_${Date.now()}`),
      tenantId,
      status:        InvoiceStatus.Open,
      lineItems:     Object.freeze(lineItems),
      subtotalCents,
      taxCents,
      totalCents,
      dueDate,
      paidAt:        null,
    };

    // Would normally be async but simplified here
    const stored: Invoice = {
      ...invoice,
      id:        invoice.invoiceId,
      createdAt: new Date(),
      updatedAt: new Date(),
      deletedAt: null,
    };

    this.repo["store"].set(stored.id, stored);
    this.emit({ event: "invoice.created", invoice: stored });
    return Ok(stored);
  }

  async markPaid(invoiceId: InvoiceId): Promise<Result<Invoice, string>> {
    const invoice = await this.repo.findById(invoiceId);
    if (!invoice)                           return Err("Invoice not found.");
    if (invoice.status === InvoiceStatus.Paid) return Err("Already paid.");

    const updated = await this.repo.update(invoiceId, {
      status: InvoiceStatus.Paid,
      paidAt: new Date(),
    });

    if (!updated) return Err("Update failed.");
    this.emit({ event: "invoice.paid", invoice: updated, paidAt: updated.paidAt! });
    return Ok(updated);
  }

  async getOverdueInvoices(): Promise<Invoice[]> {
    const now = new Date();
    const all = [...this.repo["store"].values()] as Invoice[];
    return all.filter(
      (inv) => inv.status === InvoiceStatus.Open && inv.dueDate < now
    );
  }

  formatAmount(cents: Cents, currency = "GBP"): string {
    return new Intl.NumberFormat("en-GB", {
      style: "currency", currency,
    }).format(cents / 100);
  }
}

// ---------------------------------------------------------------------------
// Decorator (experimental)
// ---------------------------------------------------------------------------

function log(target: any, key: string, descriptor: PropertyDescriptor) {
  const original = descriptor.value;
  descriptor.value = function (...args: unknown[]) {
    console.log(`[LOG] ${key}(${args.map((a) => JSON.stringify(a)).join(", ")})`);
    return original.apply(this, args);
  };
  return descriptor;
}

// ---------------------------------------------------------------------------
// Namespace
// ---------------------------------------------------------------------------

namespace Reporting {
  export interface SummaryRow {
    tenantId:   TenantId;
    planId:     PlanId;
    totalBilled: Cents;
    invoiceCount: number;
  }

  export function buildSummary(invoices: Invoice[]): SummaryRow[] {
    const grouped = new Map<TenantId, { total: number; count: number }>();
    for (const inv of invoices) {
      const existing = grouped.get(inv.tenantId) ?? { total: 0, count: 0 };
      grouped.set(inv.tenantId, {
        total: existing.total + inv.totalCents,
        count: existing.count + 1,
      });
    }
    return [...grouped.entries()].map(([tenantId, { total, count }]) => ({
      tenantId,
      planId:       "pro" as PlanId,
      totalBilled:  toCents(total),
      invoiceCount: count,
    }));
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  console.log("Billing System Demo\n");

  const billing = new BillingService({ taxRatePct: 0.20, gracePeriodDays: 14 });

  billing.on("invoice.created", ({ invoice }) => {
    console.log(`Invoice created: ${invoice.invoiceId}`);
  });

  billing.on("invoice.paid", ({ invoice }) => {
    console.log(`Invoice paid: ${invoice.invoiceId} at ${invoice.paidAt?.toISOString()}`);
  });

  const tenantId = toTenantId("tenant_abc123");
  const lineItems: LineItem[] = [
    {
      description: "Pro Plan — January 2025",
      quantity:    1,
      unitCents:   toCents(4900),
      totalCents:  toCents(4900),
    },
    {
      description: "Additional seats (5 × £10)",
      quantity:    5,
      unitCents:   toCents(1000),
      totalCents:  toCents(5000),
    },
  ];

  const dueDate = new Date(Date.now() + 30 * 86400 * 1000);
  const createResult = billing.createInvoice(tenantId, lineItems, dueDate);

  if (!createResult.ok) {
    console.error("Failed:", createResult.error);
    return;
  }

  const invoice = createResult.value;
  console.log(`Subtotal : ${billing.formatAmount(invoice.subtotalCents)}`);
  console.log(`Tax (20%): ${billing.formatAmount(invoice.taxCents)}`);
  console.log(`Total    : ${billing.formatAmount(invoice.totalCents)}`);

  const payResult = await billing.markPaid(invoice.invoiceId);
  if (payResult.ok) {
    console.log(`Status   : ${payResult.value.status}`);
  }

  // Type guard demo
  const unknown: unknown = invoice;
  if (isInvoice(unknown)) {
    console.log(`\nType guard OK — invoice status: ${unknown.status}`);
  }

  // Mapped type demo
  const planKeys = Object.keys(PLANS) as PlanId[];
  console.log("\nAvailable plans:");
  planKeys.forEach((id) => {
    const plan = PLANS[id];
    console.log(`  ${plan.name}: ${billing.formatAmount(toCents(plan.monthlyPrice))}/mo, ${plan.seats} seats`);
  });

  // Reporting namespace
  const summary = Reporting.buildSummary([invoice]);
  console.log("\nBilling summary:", summary);

  // Result chaining
  const formatted = mapResult(
    Ok(invoice.totalCents),
    (c) => billing.formatAmount(c),
  );
  console.log("\nFormatted total via Result:", formatted.ok ? formatted.value : "error");
}

main().catch(console.error);
