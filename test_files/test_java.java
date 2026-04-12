// test_java.java
//
// Holistic Java showcase — distributed task scheduling platform.
//
// Covers: interfaces, generics, enums, records, sealed classes,
// pattern matching instanceof, switch expressions, streams, lambdas,
// optional, CompletableFuture, ExecutorService, atomic types, locks,
// custom exceptions, annotations, functional interfaces, collectors,
// var keyword, text blocks, builder pattern, and more.

package com.example.scheduler;

import java.time.*;
import java.time.format.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.concurrent.locks.*;
import java.util.function.*;
import java.util.logging.*;
import java.util.stream.*;

// ===========================================================================
// Annotations
// ===========================================================================

@interface Audited {
    String by() default "system";
    String reason();
}

@interface NonNull {}
@interface Nullable {}

// ===========================================================================
// Enums
// ===========================================================================

enum Priority {
    LOW(1), NORMAL(5), HIGH(10), CRITICAL(100);

    private final int weight;
    Priority(int w) { this.weight = w; }
    public int weight() { return weight; }

    public static Priority fromWeight(int w) {
        return Arrays.stream(values())
            .filter(p -> p.weight == w)
            .findFirst()
            .orElse(NORMAL);
    }
}

enum TaskStatus {
    QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED, TIMED_OUT;

    public boolean isTerminal() {
        return switch (this) {
            case COMPLETED, FAILED, CANCELLED, TIMED_OUT -> true;
            default -> false;
        };
    }
}

// ===========================================================================
// Sealed class hierarchy — scheduling strategies
// ===========================================================================

sealed interface SchedulingStrategy permits
    PriorityFirstStrategy, FifoStrategy, RoundRobinStrategy {
    List<Task> arrange(List<Task> tasks);
    String describe();
}

record PriorityFirstStrategy() implements SchedulingStrategy {
    public List<Task> arrange(List<Task> tasks) {
        return tasks.stream()
            .sorted(Comparator.comparingInt((Task t) -> t.priority().weight()).reversed())
            .collect(Collectors.toList());
    }
    public String describe() { return "Priority-first"; }
}

record FifoStrategy() implements SchedulingStrategy {
    public List<Task> arrange(List<Task> tasks) {
        return tasks.stream()
            .sorted(Comparator.comparing(Task::createdAt))
            .collect(Collectors.toList());
    }
    public String describe() { return "FIFO"; }
}

record RoundRobinStrategy(int buckets) implements SchedulingStrategy {
    public List<Task> arrange(List<Task> tasks) {
        var result = new ArrayList<Task>(tasks.size());
        var groups = new ArrayList<List<Task>>(buckets);
        for (int i = 0; i < buckets; i++) groups.add(new ArrayList<>());
        for (int i = 0; i < tasks.size(); i++) groups.get(i % buckets).add(tasks.get(i));
        groups.forEach(result::addAll);
        return result;
    }
    public String describe() { return "Round-robin (" + buckets + " buckets)"; }
}

// ===========================================================================
// Custom exceptions
// ===========================================================================

class SchedulerException extends RuntimeException {
    private final String code;
    SchedulerException(String message, String code) {
        super(message);
        this.code = code;
    }
    public String code() { return code; }
}

class TaskTimeoutException extends SchedulerException {
    TaskTimeoutException(String taskId) {
        super("Task timed out: " + taskId, "ERR_TIMEOUT");
    }
}

class CapacityException extends SchedulerException {
    CapacityException(int max) {
        super("Scheduler at capacity (max " + max + ")", "ERR_CAPACITY");
    }
}

// ===========================================================================
// Records
// ===========================================================================

record TaskMetrics(
    String taskId,
    TaskStatus finalStatus,
    Duration elapsed,
    Optional<String> result,
    Optional<Throwable> error
) {
    boolean isSuccess() {
        return finalStatus == TaskStatus.COMPLETED && result.isPresent();
    }

    String summary() {
        return String.format("[%s] %s in %dms%s",
            taskId,
            finalStatus,
            elapsed.toMillis(),
            isSuccess() ? " → " + result.get() : "");
    }
}

record WorkerStats(
    int workerId,
    AtomicInteger completedCount,
    AtomicInteger failedCount
) {
    void recordCompletion()  { completedCount.incrementAndGet(); }
    void recordFailure()     { failedCount.incrementAndGet(); }
    int  totalProcessed()    { return completedCount.get() + failedCount.get(); }
}

// ===========================================================================
// Functional interfaces
// ===========================================================================

@FunctionalInterface interface TaskAction { String execute() throws Exception; }
@FunctionalInterface interface TaskFilter { boolean test(Task task); }
@FunctionalInterface interface MetricsConsumer { void accept(TaskMetrics metrics); }

// ===========================================================================
// Task
// ===========================================================================

class Task implements Comparable<Task> {

    private static final AtomicLong ID_SEQ = new AtomicLong(1000);

    private final String     id;
    private final String     name;
    private final Priority   priority;
    private final Duration   timeout;
    private final TaskAction action;
    private final Instant    createdAt;
    private final Map<String, String> metadata;

    private volatile TaskStatus status = TaskStatus.QUEUED;
    private volatile Instant    startedAt;
    private volatile Instant    finishedAt;

    Task(String name, Priority priority, Duration timeout, TaskAction action) {
        this(name, priority, timeout, action, Map.of());
    }

    Task(String name, Priority priority, Duration timeout,
         TaskAction action, Map<String, String> metadata) {
        this.id        = "T-" + Long.toString(ID_SEQ.getAndIncrement(), 36).toUpperCase();
        this.name      = Objects.requireNonNull(name);
        this.priority  = Objects.requireNonNull(priority);
        this.timeout   = Objects.requireNonNull(timeout);
        this.action    = Objects.requireNonNull(action);
        this.metadata  = Map.copyOf(Objects.requireNonNull(metadata));
        this.createdAt = Instant.now();
    }

    // Accessors
    String   id()        { return id; }
    String   name()      { return name; }
    Priority priority()  { return priority; }
    Duration timeout()   { return timeout; }
    TaskAction action()  { return action; }
    Instant  createdAt() { return createdAt; }
    TaskStatus status()  { return status; }
    Map<String, String> metadata() { return metadata; }

    Optional<Instant> startedAt()  { return Optional.ofNullable(startedAt); }
    Optional<Instant> finishedAt() { return Optional.ofNullable(finishedAt); }

    Optional<Duration> elapsed() {
        if (startedAt == null) return Optional.empty();
        var end = finishedAt != null ? finishedAt : Instant.now();
        return Optional.of(Duration.between(startedAt, end));
    }

    void markRunning()   { status = TaskStatus.RUNNING;  startedAt  = Instant.now(); }
    void markDone()      { status = TaskStatus.COMPLETED; finishedAt = Instant.now(); }
    void markFailed()    { status = TaskStatus.FAILED;    finishedAt = Instant.now(); }
    void markCancelled() { status = TaskStatus.CANCELLED; finishedAt = Instant.now(); }
    void markTimedOut()  { status = TaskStatus.TIMED_OUT; finishedAt = Instant.now(); }

    @Override
    public int compareTo(Task other) {
        int cmp = Integer.compare(other.priority.weight(), this.priority.weight());
        return cmp != 0 ? cmp : this.createdAt.compareTo(other.createdAt);
    }

    @Override public String toString() {
        return "Task[" + id + ", " + name + ", " + priority + ", " + status + "]";
    }

    // Builder
    static Builder builder(String name) { return new Builder(name); }

    static final class Builder {
        private final String name;
        private Priority   priority = Priority.NORMAL;
        private Duration   timeout  = Duration.ofSeconds(30);
        private TaskAction action;
        private Map<String, String> metadata = new HashMap<>();

        Builder(String name) { this.name = name; }
        Builder priority(Priority p)          { this.priority = p; return this; }
        Builder timeout(Duration d)           { this.timeout  = d; return this; }
        Builder action(TaskAction a)          { this.action   = a; return this; }
        Builder meta(String k, String v)      { this.metadata.put(k, v); return this; }
        Task build() {
            Objects.requireNonNull(action, "action required");
            return new Task(name, priority, timeout, action, metadata);
        }
    }
}

// ===========================================================================
// Scheduler
// ===========================================================================

class TaskScheduler implements AutoCloseable {

    private static final Logger LOG = Logger.getLogger(TaskScheduler.class.getName());

    private final int               poolSize;
    private final SchedulingStrategy strategy;
    private final ExecutorService   pool;
    private final List<Task>        pending;
    private final List<TaskMetrics> results;
    private final ReadWriteLock     lock;
    private final List<MetricsConsumer> listeners;

    TaskScheduler(int poolSize, SchedulingStrategy strategy) {
        this.poolSize  = poolSize;
        this.strategy  = Objects.requireNonNull(strategy);
        this.pool      = Executors.newFixedThreadPool(poolSize);
        this.pending   = new ArrayList<>();
        this.results   = new ArrayList<>();
        this.lock      = new ReentrantReadWriteLock();
        this.listeners = new CopyOnWriteArrayList<>();
    }

    void onComplete(MetricsConsumer listener) {
        listeners.add(listener);
    }

    void enqueue(Task task) {
        lock.writeLock().lock();
        try {
            if (pending.size() >= poolSize * 20) throw new CapacityException(poolSize * 20);
            pending.add(Objects.requireNonNull(task));
            LOG.info(() -> "Enqueued: " + task);
        } finally {
            lock.writeLock().unlock();
        }
    }

    List<TaskMetrics> dispatchAll(Duration globalTimeout) throws InterruptedException {
        List<Task> ordered;
        lock.writeLock().lock();
        try {
            ordered = strategy.arrange(new ArrayList<>(pending));
            pending.clear();
        } finally {
            lock.writeLock().unlock();
        }

        LOG.info(() -> "Dispatching " + ordered.size() + " tasks via " + strategy.describe());

        var futures = ordered.stream()
            .map(task -> CompletableFuture.supplyAsync(() -> runTask(task), pool)
                .orTimeout(globalTimeout.toMillis(), TimeUnit.MILLISECONDS))
            .collect(Collectors.toList());

        var batch = CompletableFuture.allOf(futures.toArray(new CompletableFuture[0]));
        try {
            batch.get(globalTimeout.toMillis(), TimeUnit.MILLISECONDS);
        } catch (TimeoutException | ExecutionException ignored) { }

        lock.writeLock().lock();
        try {
            futures.stream()
                .filter(f -> f.isDone() && !f.isCompletedExceptionally())
                .map(f -> { try { return f.get(); } catch (Exception e) { return null; } })
                .filter(Objects::nonNull)
                .forEach(results::add);
        } finally {
            lock.writeLock().unlock();
        }

        return Collections.unmodifiableList(results);
    }

    private TaskMetrics runTask(Task task) {
        task.markRunning();
        var start = Instant.now();
        try {
            var future = pool.submit(() -> task.action().execute());
            var output = future.get(task.timeout().toMillis(), TimeUnit.MILLISECONDS);
            task.markDone();
            var metrics = new TaskMetrics(
                task.id(), TaskStatus.COMPLETED,
                Duration.between(start, Instant.now()),
                Optional.of(output), Optional.empty()
            );
            listeners.forEach(l -> l.accept(metrics));
            return metrics;

        } catch (TimeoutException e) {
            task.markTimedOut();
            return new TaskMetrics(task.id(), TaskStatus.TIMED_OUT,
                Duration.between(start, Instant.now()),
                Optional.empty(), Optional.of(new TaskTimeoutException(task.id())));

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            task.markCancelled();
            return new TaskMetrics(task.id(), TaskStatus.CANCELLED,
                Duration.between(start, Instant.now()),
                Optional.empty(), Optional.of(e));

        } catch (Exception e) {
            task.markFailed();
            LOG.severe(() -> "Task " + task.id() + " failed: " + e.getMessage());
            return new TaskMetrics(task.id(), TaskStatus.FAILED,
                Duration.between(start, Instant.now()),
                Optional.empty(), Optional.of(e));
        }
    }

    List<TaskMetrics> query(TaskFilter filter) {
        lock.readLock().lock();
        try {
            return results.stream()
                .filter(m -> {
                    // Pattern matching instanceof
                    return m.finalStatus() != TaskStatus.CANCELLED;
                })
                .collect(Collectors.toList());
        } finally {
            lock.readLock().unlock();
        }
    }

    void printSummary() {
        lock.readLock().lock();
        try {
            var counts = results.stream()
                .collect(Collectors.groupingBy(TaskMetrics::finalStatus, Collectors.counting()));

            var avgMs = results.stream()
                .mapToLong(m -> m.elapsed().toMillis())
                .average();

            System.out.println("\n===== Scheduler Report =====");
            System.out.println("  Strategy  : " + strategy.describe());
            System.out.println("  Total     : " + results.size());
            counts.forEach((s, c) -> System.out.printf("  %-12s: %d%n", s, c));
            avgMs.ifPresent(ms -> System.out.printf("  Avg time  : %.1f ms%n", ms));

            // Text block
            var report = """
                Summary complete.
                All tasks processed by the %s strategy.
                """.formatted(strategy.describe());
            System.out.println(report);
        } finally {
            lock.readLock().unlock();
        }
    }

    @Override public void close() { pool.shutdownNow(); }
}

// ===========================================================================
// Main
// ===========================================================================

public class test_java {
    public static void main(String[] args) throws Exception {

        // Switch expression on sealed type
        SchedulingStrategy strategy = new PriorityFirstStrategy();
        String stratName = switch (strategy) {
            case PriorityFirstStrategy p  -> "Priority";
            case FifoStrategy f           -> "FIFO";
            case RoundRobinStrategy r     -> "RoundRobin/" + r.buckets();
        };
        System.out.println("Using strategy: " + stratName);

        try (var scheduler = new TaskScheduler(4, strategy)) {

            scheduler.onComplete(m -> System.out.println("Done: " + m.summary()));

            // Builder pattern
            scheduler.enqueue(Task.builder("DB health check")
                .priority(Priority.CRITICAL)
                .timeout(Duration.ofSeconds(3))
                .action(() -> { Thread.sleep(80); return "DB OK — 4ms latency"; })
                .meta("team", "platform")
                .build());

            scheduler.enqueue(Task.builder("Generate report")
                .priority(Priority.HIGH)
                .timeout(Duration.ofSeconds(10))
                .action(() -> { Thread.sleep(200); return "Report written: 12 pages"; })
                .build());

            scheduler.enqueue(Task.builder("Send emails")
                .priority(Priority.NORMAL)
                .timeout(Duration.ofSeconds(5))
                .action(() -> { Thread.sleep(150); return "Sent 47 emails"; })
                .build());

            scheduler.enqueue(Task.builder("Archive logs")
                .priority(Priority.LOW)
                .timeout(Duration.ofSeconds(8))
                .action(() -> { Thread.sleep(300); return "Archived 2,048 files"; })
                .build());

            scheduler.enqueue(Task.builder("Timeout demo")
                .priority(Priority.LOW)
                .timeout(Duration.ofMillis(50))
                .action(() -> { Thread.sleep(500); return "never"; })
                .build());

            var metrics = scheduler.dispatchAll(Duration.ofSeconds(30));

            // Stream operations
            var successes = metrics.stream()
                .filter(TaskMetrics::isSuccess)
                .sorted(Comparator.comparing(m -> m.elapsed().toMillis()))
                .collect(Collectors.toList());

            System.out.println("\nSuccessful tasks (fastest first):");
            successes.forEach(m -> System.out.println("  " + m.summary()));

            var byStatus = metrics.stream()
                .collect(Collectors.groupingBy(
                    TaskMetrics::finalStatus,
                    Collectors.summarizingLong(m -> m.elapsed().toMillis())
                ));

            System.out.println("\nStats by status:");
            byStatus.forEach((s, stats) ->
                System.out.printf("  %-12s count=%d avg=%.0fms%n",
                    s, stats.getCount(), stats.getAverage()));

            scheduler.printSummary();

            // Optional chaining
            metrics.stream()
                .filter(m -> m.finalStatus() == TaskStatus.TIMED_OUT)
                .findFirst()
                .flatMap(m -> m.error())
                .ifPresent(e -> System.out.println("Timeout error: " + e.getMessage()));
        }
    }
}
