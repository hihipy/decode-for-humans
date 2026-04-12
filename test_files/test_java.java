// test_java.java
//
// Task scheduling system for a distributed job-processing platform.
//
// Models tasks with priorities and deadlines, manages a worker pool,
// dispatches tasks using a pluggable scheduling strategy, and records
// execution results. Demonstrates interfaces, generics, enums, streams,
// lambdas, optionals, and custom exceptions.

package com.example.scheduler;

import java.time.Duration;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Predicate;
import java.util.logging.Logger;
import java.util.stream.Collectors;


// ===========================================================================
// Enums
// ===========================================================================

/** Execution states a task can move through during its lifecycle. */
enum TaskStatus {
    QUEUED,
    RUNNING,
    COMPLETED,
    FAILED,
    CANCELLED,
    TIMED_OUT;

    /** @return True if the task has reached a terminal state. */
    public boolean isTerminal() {
        return this == COMPLETED || this == FAILED
            || this == CANCELLED  || this == TIMED_OUT;
    }
}

/** Priority levels used to order tasks in the scheduler queue. */
enum Priority {
    LOW(1), NORMAL(5), HIGH(10), CRITICAL(100);

    private final int weight;

    Priority(int weight) { this.weight = weight; }

    /** @return The numeric weight used for comparison and sorting. */
    public int getWeight() { return weight; }
}


// ===========================================================================
// Custom exceptions
// ===========================================================================

/** Thrown when a task exceeds its configured deadline. */
class TaskTimeoutException extends RuntimeException {
    private final String taskId;

    public TaskTimeoutException(String taskId, Duration elapsed) {
        super(String.format("Task %s timed out after %s", taskId, elapsed));
        this.taskId = taskId;
    }

    public String getTaskId() { return taskId; }
}

/** Thrown when the worker pool cannot accept additional tasks. */
class SchedulerCapacityException extends Exception {
    public SchedulerCapacityException(int capacity) {
        super("Scheduler at capacity: max " + capacity + " concurrent tasks");
    }
}


// ===========================================================================
// Core domain — Task
// ===========================================================================

/**
 * Represents a single unit of work managed by the scheduler.
 *
 * <p>Tasks are immutable after construction except for mutable status and
 * timing metadata updated by the scheduler.
 */
class Task implements Comparable<Task> {

    private static final AtomicLong ID_SEQUENCE = new AtomicLong(1_000L);

    private final String     id;
    private final String     name;
    private final Priority   priority;
    private final Instant    createdAt;
    private final Duration   timeout;
    private final Callable<String> action;

    private volatile TaskStatus status    = TaskStatus.QUEUED;
    private volatile Instant    startedAt;
    private volatile Instant    finishedAt;
    private volatile String     resultMessage;
    private volatile Throwable  error;

    /**
     * Construct a new task.
     *
     * @param name     Human-readable label for logging and reporting.
     * @param priority Determines ordering in the scheduler queue.
     * @param timeout  Maximum allowed execution duration.
     * @param action   The callable that performs the actual work.
     */
    public Task(
            String name,
            Priority priority,
            Duration timeout,
            Callable<String> action) {
        this.id        = "T-" + ID_SEQUENCE.getAndIncrement();
        this.name      = Objects.requireNonNull(name,     "name");
        this.priority  = Objects.requireNonNull(priority, "priority");
        this.timeout   = Objects.requireNonNull(timeout,  "timeout");
        this.action    = Objects.requireNonNull(action,   "action");
        this.createdAt = Instant.now();
    }

    // Accessors
    public String   getId()        { return id; }
    public String   getName()      { return name; }
    public Priority getPriority()  { return priority; }
    public Duration getTimeout()   { return timeout; }
    public Instant  getCreatedAt() { return createdAt; }
    public TaskStatus getStatus()  { return status; }

    public Optional<Instant>   getStartedAt()     { return Optional.ofNullable(startedAt); }
    public Optional<Instant>   getFinishedAt()    { return Optional.ofNullable(finishedAt); }
    public Optional<String>    getResultMessage() { return Optional.ofNullable(resultMessage); }
    public Optional<Throwable> getError()         { return Optional.ofNullable(error); }

    /** @return Wall-clock execution duration, or empty if not yet started. */
    public Optional<Duration> getElapsed() {
        if (startedAt == null) return Optional.empty();
        Instant end = finishedAt != null ? finishedAt : Instant.now();
        return Optional.of(Duration.between(startedAt, end));
    }

    void markRunning()  { status = TaskStatus.RUNNING;  startedAt = Instant.now(); }
    void markDone(String result) {
        status        = TaskStatus.COMPLETED;
        resultMessage = result;
        finishedAt    = Instant.now();
    }
    void markFailed(Throwable cause) {
        status     = TaskStatus.FAILED;
        error      = cause;
        finishedAt = Instant.now();
    }
    void markCancelled() { status = TaskStatus.CANCELLED; finishedAt = Instant.now(); }
    void markTimedOut()  { status = TaskStatus.TIMED_OUT;  finishedAt = Instant.now(); }

    Callable<String> getAction() { return action; }

    /** Higher-priority tasks sort first; ties broken by creation time. */
    @Override
    public int compareTo(Task other) {
        int cmp = Integer.compare(
            other.priority.getWeight(), this.priority.getWeight()
        );
        return cmp != 0 ? cmp : this.createdAt.compareTo(other.createdAt);
    }

    @Override
    public String toString() {
        return String.format("Task[%s, %s, %s, %s]",
            id, name, priority, status);
    }
}


// ===========================================================================
// Scheduling strategy (Strategy pattern)
// ===========================================================================

/** Pluggable interface for ordering tasks before dispatch. */
interface SchedulingStrategy {

    /**
     * Sort or reorder the pending task list in place.
     *
     * @param tasks Mutable list of queued tasks.
     */
    void arrange(List<Task> tasks);

    /** @return A short human-readable description of this strategy. */
    String describe();
}

/** Dispatches the highest-priority task first. */
class PriorityFirstStrategy implements SchedulingStrategy {

    @Override
    public void arrange(List<Task> tasks) {
        Collections.sort(tasks);
    }

    @Override
    public String describe() { return "Priority-first (highest weight → first)"; }
}

/** Dispatches the task with the oldest creation timestamp first. */
class FifoStrategy implements SchedulingStrategy {

    @Override
    public void arrange(List<Task> tasks) {
        tasks.sort(Comparator.comparing(Task::getCreatedAt));
    }

    @Override
    public String describe() { return "FIFO (oldest enqueued → first)"; }
}


// ===========================================================================
// Execution result
// ===========================================================================

/**
 * Immutable snapshot of a completed task's outcome.
 *
 * @param <T> The type of the result value produced.
 */
record ExecutionResult<T>(
    String     taskId,
    String     taskName,
    TaskStatus finalStatus,
    Optional<T>         result,
    Optional<Throwable> error,
    Duration   elapsed
) {
    /** @return True if the task completed without error. */
    public boolean isSuccess() {
        return finalStatus == TaskStatus.COMPLETED && result.isPresent();
    }
}


// ===========================================================================
// Task scheduler
// ===========================================================================

/**
 * Manages a bounded thread pool and dispatches tasks according to a
 * configurable scheduling strategy.
 *
 * <p>Tasks are enqueued, sorted by the active strategy, and executed
 * concurrently up to the configured pool capacity.
 */
class TaskScheduler implements AutoCloseable {

    private static final Logger log = Logger.getLogger(TaskScheduler.class.getName());

    private final int                  poolSize;
    private final SchedulingStrategy   strategy;
    private final ExecutorService      pool;
    private final List<Task>           pending;
    private final List<ExecutionResult<String>> completedResults;

    /**
     * Construct a scheduler with a fixed thread pool.
     *
     * @param poolSize Maximum number of concurrently executing tasks.
     * @param strategy The ordering strategy applied before each dispatch.
     */
    public TaskScheduler(int poolSize, SchedulingStrategy strategy) {
        this.poolSize         = poolSize;
        this.strategy         = Objects.requireNonNull(strategy, "strategy");
        this.pool             = Executors.newFixedThreadPool(poolSize);
        this.pending          = new ArrayList<>();
        this.completedResults = new ArrayList<>();
    }

    /**
     * Add a task to the pending queue.
     *
     * @param task The task to enqueue.
     * @throws SchedulerCapacityException If the queue has grown too large.
     */
    public synchronized void enqueue(Task task) throws SchedulerCapacityException {
        if (pending.size() >= poolSize * 10) {
            throw new SchedulerCapacityException(poolSize * 10);
        }
        pending.add(Objects.requireNonNull(task, "task"));
        log.info(() -> "Enqueued: " + task);
    }

    /**
     * Dispatch all pending tasks to the thread pool and wait for completion.
     *
     * @param globalTimeout Maximum total wait time for all tasks to finish.
     * @return List of execution results in completion order.
     * @throws InterruptedException If the calling thread is interrupted.
     */
    public List<ExecutionResult<String>> dispatchAll(Duration globalTimeout)
            throws InterruptedException {

        strategy.arrange(pending);
        log.info(() -> "Dispatching " + pending.size()
            + " tasks using strategy: " + strategy.describe());

        List<Future<ExecutionResult<String>>> futures = pending.stream()
            .map(task -> pool.submit(() -> executeTask(task)))
            .collect(Collectors.toList());

        pending.clear();

        long deadlineMs = System.currentTimeMillis() + globalTimeout.toMillis();

        for (Future<ExecutionResult<String>> future : futures) {
            long remainingMs = deadlineMs - System.currentTimeMillis();
            if (remainingMs <= 0) {
                future.cancel(true);
                continue;
            }
            try {
                ExecutionResult<String> result = future.get(remainingMs, TimeUnit.MILLISECONDS);
                completedResults.add(result);
            } catch (TimeoutException e) {
                future.cancel(true);
                log.warning("Global timeout reached — cancelling remaining tasks");
            } catch (ExecutionException e) {
                log.severe("Unexpected executor error: " + e.getCause());
            }
        }

        return Collections.unmodifiableList(completedResults);
    }

    /**
     * Run a single task within its own timeout window and produce a result.
     *
     * @param task The task to execute.
     * @return An ExecutionResult capturing the outcome.
     */
    private ExecutionResult<String> executeTask(Task task) {
        task.markRunning();
        Instant start = Instant.now();

        try {
            Future<String> actionFuture = pool.submit(task.getAction());
            String output = actionFuture.get(task.getTimeout().toMillis(), TimeUnit.MILLISECONDS);
            task.markDone(output);
            log.info(() -> "Completed: " + task.getId());
            return new ExecutionResult<>(
                task.getId(), task.getName(), TaskStatus.COMPLETED,
                Optional.of(output), Optional.empty(),
                Duration.between(start, Instant.now())
            );

        } catch (TimeoutException e) {
            task.markTimedOut();
            log.warning(() -> "Timed out: " + task.getId());
            return new ExecutionResult<>(
                task.getId(), task.getName(), TaskStatus.TIMED_OUT,
                Optional.empty(),
                Optional.of(new TaskTimeoutException(task.getId(), task.getTimeout())),
                Duration.between(start, Instant.now())
            );

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            task.markCancelled();
            return new ExecutionResult<>(
                task.getId(), task.getName(), TaskStatus.CANCELLED,
                Optional.empty(), Optional.of(e),
                Duration.between(start, Instant.now())
            );

        } catch (Exception e) {
            task.markFailed(e);
            log.severe(() -> "Failed: " + task.getId() + " — " + e.getMessage());
            return new ExecutionResult<>(
                task.getId(), task.getName(), TaskStatus.FAILED,
                Optional.empty(), Optional.of(e),
                Duration.between(start, Instant.now())
            );
        }
    }

    /**
     * Filter completed results by a custom predicate.
     *
     * @param predicate Filter condition applied to each result.
     * @return List of results matching the predicate.
     */
    public List<ExecutionResult<String>> queryResults(
            Predicate<ExecutionResult<String>> predicate) {
        return completedResults.stream()
            .filter(predicate)
            .collect(Collectors.toList());
    }

    /** Print a summary of outcomes to standard output. */
    public void printSummary() {
        Map<TaskStatus, Long> counts = completedResults.stream()
            .collect(Collectors.groupingBy(
                ExecutionResult::finalStatus, Collectors.counting()
            ));

        OptionalDouble avgMs = completedResults.stream()
            .mapToLong(r -> r.elapsed().toMillis())
            .average();

        System.out.println("\n===== Scheduler Summary =====");
        System.out.println("  Total tasks : " + completedResults.size());
        counts.forEach((status, count) ->
            System.out.printf("  %-12s : %d%n", status, count));
        avgMs.ifPresent(ms ->
            System.out.printf("  Avg duration: %.1f ms%n", ms));
        System.out.println("=============================\n");
    }

    @Override
    public void close() {
        pool.shutdownNow();
    }
}


// ===========================================================================
// Entry point
// ===========================================================================

public class test_java {

    public static void main(String[] args) throws Exception {

        SchedulingStrategy strategy = new PriorityFirstStrategy();

        try (TaskScheduler scheduler = new TaskScheduler(4, strategy)) {

            // Enqueue a mix of tasks at different priority levels
            scheduler.enqueue(new Task(
                "Generate weekly report",
                Priority.HIGH,
                Duration.ofSeconds(10),
                () -> { Thread.sleep(200); return "Report generated OK"; }
            ));

            scheduler.enqueue(new Task(
                "Send notification emails",
                Priority.NORMAL,
                Duration.ofSeconds(5),
                () -> { Thread.sleep(150); return "Emails dispatched: 42"; }
            ));

            scheduler.enqueue(new Task(
                "Archive old logs",
                Priority.LOW,
                Duration.ofSeconds(8),
                () -> { Thread.sleep(300); return "Archived 1,204 log files"; }
            ));

            scheduler.enqueue(new Task(
                "Database health check",
                Priority.CRITICAL,
                Duration.ofSeconds(3),
                () -> { Thread.sleep(100); return "DB reachable — latency 4 ms"; }
            ));

            scheduler.enqueue(new Task(
                "Intentional timeout task",
                Priority.LOW,
                Duration.ofMillis(50),
                () -> { Thread.sleep(500); return "This should never appear"; }
            ));

            // Run all tasks with a 30-second global ceiling
            List<ExecutionResult<String>> results =
                scheduler.dispatchAll(Duration.ofSeconds(30));

            // Print successes
            System.out.println("Successful results:");
            results.stream()
                .filter(ExecutionResult::isSuccess)
                .forEach(r -> System.out.printf(
                    "  [%s] %s → %s (%d ms)%n",
                    r.taskId(), r.taskName(),
                    r.result().orElse(""),
                    r.elapsed().toMillis()
                ));

            // Print failures
            List<ExecutionResult<String>> failures =
                scheduler.queryResults(r -> !r.isSuccess());

            if (!failures.isEmpty()) {
                System.out.println("Non-successful tasks:");
                failures.forEach(r -> System.out.printf(
                    "  [%s] %s — %s%n",
                    r.taskId(), r.taskName(), r.finalStatus()
                ));
            }

            scheduler.printSummary();
        }
    }
}
