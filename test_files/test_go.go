// test_go.go
//
// Holistic Go showcase — distributed rate limiter and API gateway.
//
// Covers: interfaces, structs, embedding, goroutines, channels, select,
// context, sync primitives (Mutex, RWMutex, WaitGroup, Once),
// generics, error wrapping, defer, panic/recover, closures, variadic
// functions, method sets, type assertions, type switches, init(),
// build tags concept, table-driven tests pattern, functional options,
// io interfaces, and standard library patterns.

package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"
)

// ===========================================================================
// Sentinel errors
// ===========================================================================

var (
	ErrRateLimited    = errors.New("rate limited")
	ErrCircuitOpen    = errors.New("circuit breaker open")
	ErrTimeout        = errors.New("request timed out")
	ErrUnauthorised   = errors.New("unauthorised")
	ErrNotFound       = errors.New("not found")
)

// ===========================================================================
// Generic Result
// ===========================================================================

type Result[T any] struct {
	Value T
	Err   error
}

func Ok[T any](v T) Result[T]  { return Result[T]{Value: v} }
func Err[T any](e error) Result[T] { return Result[T]{Err: e} }

func (r Result[T]) IsOk() bool  { return r.Err == nil }
func (r Result[T]) Unwrap() (T, error) { return r.Value, r.Err }

func Map[T, U any](r Result[T], fn func(T) U) Result[U] {
	if r.Err != nil { return Err[U](r.Err) }
	return Ok(fn(r.Value))
}

// ===========================================================================
// Generic ordered set
// ===========================================================================

type Set[T comparable] struct {
	mu    sync.RWMutex
	items map[T]struct{}
}

func NewSet[T comparable]() *Set[T] {
	return &Set[T]{items: make(map[T]struct{})}
}

func (s *Set[T]) Add(v T) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.items[v] = struct{}{}
}

func (s *Set[T]) Contains(v T) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	_, ok := s.items[v]
	return ok
}

func (s *Set[T]) Remove(v T) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.items, v)
}

func (s *Set[T]) Len() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.items)
}

// ===========================================================================
// Rate limiter — token bucket
// ===========================================================================

type RateLimiter struct {
	tokens   float64
	maxBurst float64
	rate     float64   // tokens per second
	lastTick time.Time
	mu       sync.Mutex
}

func NewRateLimiter(rps, burst float64) *RateLimiter {
	return &RateLimiter{
		tokens:   burst,
		maxBurst: burst,
		rate:     rps,
		lastTick: time.Now(),
	}
}

func (rl *RateLimiter) Allow() bool {
	rl.mu.Lock()
	defer rl.mu.Unlock()

	now    := time.Now()
	elapsed := now.Sub(rl.lastTick).Seconds()
	rl.lastTick = now

	rl.tokens = min(rl.maxBurst, rl.tokens+elapsed*rl.rate)
	if rl.tokens < 1 {
		return false
	}
	rl.tokens--
	return true
}

func (rl *RateLimiter) Wait(ctx context.Context) error {
	for {
		if rl.Allow() { return nil }
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(10 * time.Millisecond):
		}
	}
}

// ===========================================================================
// Circuit breaker
// ===========================================================================

type CircuitState int32

const (
	StateClosed   CircuitState = iota // normal — requests pass through
	StateOpen                         // tripped — requests fail fast
	StateHalfOpen                     // testing — one request allowed
)

func (s CircuitState) String() string {
	return [...]string{"closed", "open", "half-open"}[s]
}

type CircuitBreaker struct {
	state        atomic.Int32
	failures     atomic.Int64
	successes    atomic.Int64
	lastFailure  atomic.Int64 // unix nano
	threshold    int64
	resetTimeout time.Duration
	mu           sync.Mutex
}

func NewCircuitBreaker(threshold int64, resetTimeout time.Duration) *CircuitBreaker {
	cb := &CircuitBreaker{
		threshold:    threshold,
		resetTimeout: resetTimeout,
	}
	cb.state.Store(int32(StateClosed))
	return cb
}

func (cb *CircuitBreaker) State() CircuitState {
	return CircuitState(cb.state.Load())
}

func (cb *CircuitBreaker) Call(fn func() error) error {
	switch cb.State() {
	case StateOpen:
		elapsed := time.Since(time.Unix(0, cb.lastFailure.Load()))
		if elapsed < cb.resetTimeout {
			return ErrCircuitOpen
		}
		cb.state.CompareAndSwap(int32(StateOpen), int32(StateHalfOpen))
	}

	err := fn()
	if err != nil {
		cb.failures.Add(1)
		cb.lastFailure.Store(time.Now().UnixNano())
		if cb.failures.Load() >= cb.threshold {
			cb.state.Store(int32(StateOpen))
		}
		return err
	}

	cb.successes.Add(1)
	if cb.State() == StateHalfOpen {
		cb.state.Store(int32(StateClosed))
		cb.failures.Store(0)
	}
	return nil
}

// ===========================================================================
// Request / Response
// ===========================================================================

type Request struct {
	ID       string
	Method   string
	Path     string
	ClientID string
	Headers  map[string]string
	Body     []byte
	ctx      context.Context
}

func (r *Request) Context() context.Context {
	if r.ctx == nil { return context.Background() }
	return r.ctx
}

type Response struct {
	StatusCode int
	Body       []byte
	Headers    map[string]string
	Latency    time.Duration
}

func (r Response) IsSuccess() bool { return r.StatusCode >= 200 && r.StatusCode < 300 }

func JSONResponse(status int, v any) Response {
	b, _ := json.Marshal(v)
	return Response{
		StatusCode: status,
		Body:       b,
		Headers:    map[string]string{"Content-Type": "application/json"},
	}
}

// ===========================================================================
// Middleware (functional options pattern)
// ===========================================================================

type Handler func(ctx context.Context, req Request) Response

type Middleware func(Handler) Handler

func Chain(h Handler, mws ...Middleware) Handler {
	for i := len(mws) - 1; i >= 0; i-- {
		h = mws[i](h)
	}
	return h
}

func WithLogging(logger *log.Logger) Middleware {
	return func(next Handler) Handler {
		return func(ctx context.Context, req Request) Response {
			start := time.Now()
			resp  := next(ctx, req)
			resp.Latency = time.Since(start)
			logger.Printf("%s %s %d %v", req.Method, req.Path, resp.StatusCode, resp.Latency)
			return resp
		}
	}
}

func WithRateLimit(limiter *RateLimiter) Middleware {
	return func(next Handler) Handler {
		return func(ctx context.Context, req Request) Response {
			if !limiter.Allow() {
				return JSONResponse(http.StatusTooManyRequests, map[string]string{
					"error": ErrRateLimited.Error(),
				})
			}
			return next(ctx, req)
		}
	}
}

func WithCircuitBreaker(cb *CircuitBreaker) Middleware {
	return func(next Handler) Handler {
		return func(ctx context.Context, req Request) Response {
			var resp Response
			err := cb.Call(func() error {
				resp = next(ctx, req)
				if resp.StatusCode >= 500 {
					return fmt.Errorf("upstream error %d", resp.StatusCode)
				}
				return nil
			})
			if errors.Is(err, ErrCircuitOpen) {
				return JSONResponse(http.StatusServiceUnavailable, map[string]string{
					"error": "service temporarily unavailable",
				})
			}
			return resp
		}
	}
}

func WithTimeout(d time.Duration) Middleware {
	return func(next Handler) Handler {
		return func(ctx context.Context, req Request) Response {
			ctx, cancel := context.WithTimeout(ctx, d)
			defer cancel()

			ch := make(chan Response, 1)
			go func() { ch <- next(ctx, req) }()

			select {
			case resp := <-ch:
				return resp
			case <-ctx.Done():
				return JSONResponse(http.StatusGatewayTimeout, map[string]string{
					"error": ErrTimeout.Error(),
				})
			}
		}
	}
}

// ===========================================================================
// Route registry
// ===========================================================================

type RouteKey struct {
	Method string
	Path   string
}

type Router struct {
	routes map[RouteKey]Handler
	mu     sync.RWMutex
}

func NewRouter() *Router {
	return &Router{routes: make(map[RouteKey]Handler)}
}

func (r *Router) Handle(method, path string, h Handler, mws ...Middleware) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.routes[RouteKey{method, path}] = Chain(h, mws...)
}

func (r *Router) Dispatch(ctx context.Context, req Request) Response {
	r.mu.RLock()
	h, ok := r.routes[RouteKey{req.Method, req.Path}]
	r.mu.RUnlock()

	if !ok {
		return JSONResponse(http.StatusNotFound, map[string]string{"error": ErrNotFound.Error()})
	}
	return h(ctx, req)
}

// ===========================================================================
// Worker pool
// ===========================================================================

type Job struct {
	ID  string
	Req Request
}

type WorkerPool struct {
	jobs    chan Job
	results chan Result[Response]
	wg      sync.WaitGroup
	router  *Router
	once    sync.Once
}

func NewWorkerPool(workers int, bufSize int, router *Router) *WorkerPool {
	wp := &WorkerPool{
		jobs:    make(chan Job, bufSize),
		results: make(chan Result[Response], bufSize),
		router:  router,
	}
	for i := 0; i < workers; i++ {
		wp.wg.Add(1)
		go wp.worker(i)
	}
	return wp
}

func (wp *WorkerPool) worker(id int) {
	defer wp.wg.Done()
	defer func() {
		if r := recover(); r != nil {
			log.Printf("worker %d recovered from panic: %v", id, r)
		}
	}()

	for job := range wp.jobs {
		resp := wp.router.Dispatch(job.Req.Context(), job.Req)
		wp.results <- Ok(resp)
	}
}

func (wp *WorkerPool) Submit(job Job) {
	wp.jobs <- job
}

func (wp *WorkerPool) Results() <-chan Result[Response] { return wp.results }

func (wp *WorkerPool) Shutdown() {
	wp.once.Do(func() {
		close(wp.jobs)
		wp.wg.Wait()
		close(wp.results)
	})
}

// ===========================================================================
// Metrics — concurrent counter map
// ===========================================================================

type Metrics struct {
	counters sync.Map
}

func (m *Metrics) Inc(key string) {
	v, _ := m.counters.LoadOrStore(key, new(atomic.Int64))
	v.(*atomic.Int64).Add(1)
}

func (m *Metrics) Get(key string) int64 {
	v, ok := m.counters.Load(key)
	if !ok { return 0 }
	return v.(*atomic.Int64).Load()
}

func (m *Metrics) Print() {
	m.counters.Range(func(k, v any) bool {
		fmt.Printf("  %-30s : %d\n", k, v.(*atomic.Int64).Load())
		return true
	})
}

// ===========================================================================
// Helper
// ===========================================================================

func min(a, b float64) float64 {
	if a < b { return a }
	return b
}

// ===========================================================================
// init
// ===========================================================================

var startTime time.Time

func init() {
	startTime = time.Now()
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	rand.New(rand.NewSource(time.Now().UnixNano()))
}

// ===========================================================================
// Main
// ===========================================================================

func main() {
	fmt.Println("API Gateway Demo\n")

	logger  := log.New(os.Stdout, "[GW] ", log.LstdFlags)
	metrics := &Metrics{}
	limiter := NewRateLimiter(100, 20)
	breaker := NewCircuitBreaker(5, 10*time.Second)

	router := NewRouter()

	// Handlers
	healthHandler := func(ctx context.Context, req Request) Response {
		metrics.Inc("requests.health")
		return JSONResponse(http.StatusOK, map[string]any{
			"status": "ok", "uptime": time.Since(startTime).String(),
		})
	}

	dataHandler := func(ctx context.Context, req Request) Response {
		metrics.Inc("requests.data")
		// Simulate occasional upstream failures
		if rand.Intn(10) == 0 {
			return JSONResponse(http.StatusInternalServerError, map[string]string{"error": "upstream"})
		}
		time.Sleep(time.Duration(rand.Intn(50)) * time.Millisecond)
		return JSONResponse(http.StatusOK, map[string]any{"data": "hello", "id": req.ID})
	}

	// Register with middleware chains
	router.Handle("GET", "/health", healthHandler,
		WithLogging(logger),
	)
	router.Handle("GET", "/data", dataHandler,
		WithLogging(logger),
		WithRateLimit(limiter),
		WithCircuitBreaker(breaker),
		WithTimeout(200*time.Millisecond),
	)

	pool := NewWorkerPool(4, 100, router)
	defer pool.Shutdown()

	// Fan-out requests
	ctx := context.Background()
	nRequests := 20
	var wg sync.WaitGroup

	// Collect results in a goroutine
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < nRequests; i++ {
			r := <-pool.Results()
			if r.IsOk() {
				resp := r.Value
				metrics.Inc(fmt.Sprintf("responses.%d", resp.StatusCode))
			}
		}
	}()

	// Submit jobs
	for i := 0; i < nRequests; i++ {
		path := "/data"
		if i%5 == 0 { path = "/health" }
		pool.Submit(Job{
			ID: fmt.Sprintf("job-%d", i),
			Req: Request{
				ID:       fmt.Sprintf("req-%d", i),
				Method:   "GET",
				Path:     path,
				ClientID: fmt.Sprintf("client-%d", i%3),
				ctx:      ctx,
			},
		})
	}

	wg.Wait()

	// Type switch demo
	values := []any{42, "hello", true, 3.14, []int{1, 2, 3}}
	for _, v := range values {
		switch t := v.(type) {
		case int:         fmt.Printf("int: %d\n", t)
		case string:      fmt.Printf("string: %q\n", t)
		case bool:        fmt.Printf("bool: %v\n", t)
		case float64:     fmt.Printf("float64: %f\n", t)
		default:          fmt.Printf("other: %T\n", t)
		}
	}

	// Generic Set demo
	s := NewSet[string]()
	for _, w := range []string{"go", "rust", "zig", "go", "rust"} {
		s.Add(w)
	}
	fmt.Printf("\nUnique languages: %d\n", s.Len())

	// Result chaining
	r := Map(Ok(42), func(n int) string { return fmt.Sprintf("answer=%d", n) })
	fmt.Println("Result map:", r.Value)

	// Circuit breaker state
	fmt.Printf("Circuit state: %s\n", breaker.State())

	// Metrics
	fmt.Println("\nMetrics:")
	metrics.Print()
	fmt.Printf("\nUptime: %v\n", time.Since(startTime))
}
