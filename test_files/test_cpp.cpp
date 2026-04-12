// test_cpp.cpp
//
// Holistic C++ showcase — high-performance in-memory cache system.
//
// Covers: templates, concepts, SFINAE, variadic templates, lambdas,
// smart pointers, RAII, move semantics, perfect forwarding, constexpr,
// type traits, std::variant, std::optional, std::expected (C++23),
// ranges, coroutines concept (simplified), operator overloading,
// custom iterators, thread safety, atomic operations, condition variables,
// allocators, structured bindings, fold expressions, and more.

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <concepts>
#include <functional>
#include <iostream>
#include <list>
#include <memory>
#include <mutex>
#include <optional>
#include <queue>
#include <ranges>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <type_traits>
#include <unordered_map>
#include <variant>
#include <vector>

namespace cache {

// ===========================================================================
// Concepts
// ===========================================================================

template<typename K>
concept Hashable = requires(K k) {
    { std::hash<K>{}(k) } -> std::convertible_to<std::size_t>;
};

template<typename T>
concept Copyable = std::copy_constructible<T> && std::copyable<T>;

template<typename T>
concept Serialisable = requires(T t) {
    { t.serialise() } -> std::convertible_to<std::string>;
};

// ===========================================================================
// Result type (simplified std::expected)
// ===========================================================================

template<typename T, typename E = std::string>
class Result {
    std::variant<T, E> _data;
public:
    static Result Ok(T value)  { return Result(std::in_place_index<0>, std::move(value)); }
    static Result Err(E error) { return Result(std::in_place_index<1>, std::move(error)); }

    template<std::size_t I, typename... Args>
    explicit Result(std::in_place_index_t<I> t, Args&&... args)
        : _data(t, std::forward<Args>(args)...) {}

    bool ok()  const { return _data.index() == 0; }
    bool err() const { return !ok(); }

    const T& value() const { return std::get<0>(_data); }
    const E& error() const { return std::get<1>(_data); }
    T&       value()       { return std::get<0>(_data); }

    template<typename F>
    auto map(F&& fn) const -> Result<std::invoke_result_t<F, T>, E> {
        if (ok()) return Result<std::invoke_result_t<F,T>,E>::Ok(fn(value()));
        return Result<std::invoke_result_t<F,T>,E>::Err(error());
    }
};

// ===========================================================================
// Cache entry
// ===========================================================================

using Clock     = std::chrono::steady_clock;
using TimePoint = Clock::time_point;
using Duration  = Clock::duration;

template<typename V>
struct CacheEntry {
    V           value;
    TimePoint   inserted_at;
    TimePoint   expires_at;
    std::size_t hit_count{0};
    std::size_t size_bytes;

    bool is_expired() const noexcept {
        return Clock::now() > expires_at;
    }

    Duration ttl_remaining() const noexcept {
        auto remaining = expires_at - Clock::now();
        return remaining.count() > 0 ? remaining : Duration{0};
    }

    // Move-only
    CacheEntry(V v, Duration ttl, std::size_t sz)
        : value(std::move(v))
        , inserted_at(Clock::now())
        , expires_at(Clock::now() + ttl)
        , size_bytes(sz) {}

    CacheEntry(CacheEntry&&)            = default;
    CacheEntry& operator=(CacheEntry&&) = default;
    CacheEntry(const CacheEntry&)       = delete;
    CacheEntry& operator=(const CacheEntry&) = delete;
};

// ===========================================================================
// Eviction policy — strategy via template parameter
// ===========================================================================

struct LRUPolicy {};
struct LFUPolicy {};
struct FIFOPolicy {};

// ===========================================================================
// Statistics
// ===========================================================================

struct CacheStats {
    std::atomic<uint64_t> hits{0};
    std::atomic<uint64_t> misses{0};
    std::atomic<uint64_t> evictions{0};
    std::atomic<uint64_t> expirations{0};
    std::atomic<uint64_t> inserts{0};
    std::atomic<uint64_t> bytes_used{0};

    double hit_rate() const noexcept {
        auto total = hits.load() + misses.load();
        return total == 0 ? 0.0 : static_cast<double>(hits) / total;
    }

    void print() const {
        std::cout << "  Hits       : " << hits        << "\n"
                  << "  Misses     : " << misses       << "\n"
                  << "  Hit rate   : " << hit_rate() * 100 << "%\n"
                  << "  Evictions  : " << evictions    << "\n"
                  << "  Expirations: " << expirations  << "\n"
                  << "  Bytes used : " << bytes_used   << "\n";
    }
};

// ===========================================================================
// Main cache template
// ===========================================================================

template<
    Hashable   K,
    Copyable   V,
    typename   Policy  = LRUPolicy,
    std::size_t MaxSize = 1024
>
class Cache {
public:
    using key_type    = K;
    using mapped_type = V;
    using entry_type  = CacheEntry<V>;
    using OnEvict     = std::function<void(const K&, const V&)>;

private:
    // LRU list: front = most recently used
    std::list<K>                                           _order;
    std::unordered_map<K, std::pair<entry_type, typename std::list<K>::iterator>> _map;

    mutable std::mutex _mu;
    std::condition_variable _cv;

    std::size_t _max_bytes;
    Duration    _default_ttl;
    OnEvict     _on_evict;
    CacheStats  _stats;

    // Background expiry thread
    std::thread            _expiry_thread;
    std::atomic<bool>      _running{true};

public:
    explicit Cache(
        std::size_t max_bytes     = 64 * 1024 * 1024,
        Duration    default_ttl   = std::chrono::minutes(5),
        OnEvict     on_evict      = nullptr
    )
        : _max_bytes(max_bytes)
        , _default_ttl(default_ttl)
        , _on_evict(std::move(on_evict))
        , _expiry_thread(&Cache::expiry_loop, this)
    {}

    ~Cache() {
        _running = false;
        _cv.notify_all();
        if (_expiry_thread.joinable()) _expiry_thread.join();
    }

    // Non-copyable, movable
    Cache(const Cache&)            = delete;
    Cache& operator=(const Cache&) = delete;
    Cache(Cache&&)                 = default;

    // ---------- Write ----------

    template<typename VV>
    Result<bool> put(const K& key, VV&& value, std::optional<Duration> ttl = std::nullopt)
    {
        auto ttl_actual   = ttl.value_or(_default_ttl);
        auto sz           = sizeof(V);
        auto new_entry    = entry_type(std::forward<VV>(value), ttl_actual, sz);

        std::unique_lock lock(_mu);

        // Remove existing entry if present
        if (auto it = _map.find(key); it != _map.end()) {
            _stats.bytes_used -= it->second.first.size_bytes;
            _order.erase(it->second.second);
            _map.erase(it);
        }

        // Evict if at capacity
        while (_map.size() >= MaxSize || _stats.bytes_used + sz > _max_bytes) {
            if (_map.empty()) return Result<bool>::Err("Entry too large for cache.");
            evict_one();
        }

        _order.push_front(key);
        _map.emplace(key, std::make_pair(std::move(new_entry), _order.begin()));
        _stats.bytes_used += sz;
        ++_stats.inserts;

        return Result<bool>::Ok(true);
    }

    // ---------- Read ----------

    std::optional<V> get(const K& key)
    {
        std::unique_lock lock(_mu);
        auto it = _map.find(key);

        if (it == _map.end()) { ++_stats.misses; return std::nullopt; }

        auto& [entry, list_it] = it->second;

        if (entry.is_expired()) {
            ++_stats.expirations;
            ++_stats.misses;
            evict_key(it);
            return std::nullopt;
        }

        ++entry.hit_count;
        ++_stats.hits;

        // LRU: move to front
        if constexpr (std::is_same_v<Policy, LRUPolicy>) {
            _order.erase(list_it);
            _order.push_front(key);
            list_it = _order.begin();
        }

        return entry.value;
    }

    // ---------- Query ----------

    bool contains(const K& key) const {
        std::shared_lock lock(_mu);  // conceptual — use shared_mutex in real code
        auto it = _map.find(key);
        return it != _map.end() && !it->second.first.is_expired();
    }

    bool remove(const K& key) {
        std::unique_lock lock(_mu);
        auto it = _map.find(key);
        if (it == _map.end()) return false;
        evict_key(it);
        return true;
    }

    void clear() {
        std::unique_lock lock(_mu);
        _map.clear();
        _order.clear();
        _stats.bytes_used = 0;
    }

    std::size_t size()  const { std::unique_lock l(_mu); return _map.size(); }
    std::size_t bytes() const { return _stats.bytes_used.load(); }

    const CacheStats& stats() const { return _stats; }

    // Operator[] — returns optional<V>
    std::optional<V> operator[](const K& key) { return get(key); }

    // ---------- Iteration (snapshot) ----------

    std::vector<std::pair<K,V>> snapshot() const {
        std::unique_lock lock(_mu);
        std::vector<std::pair<K,V>> result;
        result.reserve(_map.size());
        for (auto& [k, pv] : _map) {
            if (!pv.first.is_expired())
                result.emplace_back(k, pv.first.value);
        }
        return result;
    }

private:
    using MapIter = typename decltype(_map)::iterator;

    void evict_one() {
        if (_order.empty()) return;
        evict_key(_map.find(_order.back()));
    }

    void evict_key(MapIter it) {
        if (it == _map.end()) return;
        if (_on_evict) _on_evict(it->first, it->second.first.value);
        _stats.bytes_used -= it->second.first.size_bytes;
        _order.erase(it->second.second);
        _map.erase(it);
        ++_stats.evictions;
    }

    void expiry_loop() {
        while (_running) {
            std::unique_lock lock(_mu);
            _cv.wait_for(lock, std::chrono::seconds(30),
                         [this]{ return !_running.load(); });

            for (auto it = _map.begin(); it != _map.end(); ) {
                if (it->second.first.is_expired()) {
                    ++_stats.expirations;
                    _stats.bytes_used -= it->second.first.size_bytes;
                    _order.erase(it->second.second);
                    it = _map.erase(it);
                } else {
                    ++it;
                }
            }
        }
    }
};

// ===========================================================================
// Variadic helpers
// ===========================================================================

// Fold expression — sum sizes
template<typename... Ts>
constexpr std::size_t total_size() {
    return (sizeof(Ts) + ...);
}

// Perfect forwarding factory
template<typename C, typename K, typename V, typename... Args>
bool emplace(C& cache, K&& key, Args&&... args) {
    return cache.put(
        std::forward<K>(key),
        V(std::forward<Args>(args)...)
    ).ok();
}

// ===========================================================================
// Compile-time checks (constexpr)
// ===========================================================================

constexpr bool is_power_of_two(std::size_t n) noexcept {
    return n > 0 && (n & (n - 1)) == 0;
}

static_assert(is_power_of_two(1024), "MaxSize should be a power of two.");
static_assert(total_size<int, double, char>() == sizeof(int) + sizeof(double) + sizeof(char));

// ===========================================================================
// RAII guard
// ===========================================================================

template<typename F>
class ScopeGuard {
    F _fn;
    bool _active;
public:
    explicit ScopeGuard(F fn) : _fn(std::move(fn)), _active(true) {}
    ~ScopeGuard() { if (_active) _fn(); }
    void release()      { _active = false; }
    ScopeGuard(ScopeGuard&&)            = default;
    ScopeGuard(const ScopeGuard&)       = delete;
};

template<typename F>
ScopeGuard<F> make_scope_guard(F fn) { return ScopeGuard<F>(std::move(fn)); }

// ===========================================================================
// Visitor via std::variant
// ===========================================================================

using ConfigValue = std::variant<int, double, std::string, bool>;

struct ConfigPrinter {
    void operator()(int i)               const { std::cout << "int: "    << i         << "\n"; }
    void operator()(double d)            const { std::cout << "double: " << d         << "\n"; }
    void operator()(const std::string& s)const { std::cout << "str: "    << s         << "\n"; }
    void operator()(bool b)              const { std::cout << "bool: "   << std::boolalpha << b << "\n"; }
};

} // namespace cache

// ===========================================================================
// Main
// ===========================================================================

int main() {
    using namespace cache;
    using namespace std::chrono_literals;

    std::cout << "In-Memory Cache Demo\n\n";

    // Create LRU cache: max 1024 entries, 64 MB, 1-minute TTL
    Cache<std::string, std::string> lru_cache(
        64 * 1024 * 1024,
        60s,
        [](const std::string& key, const std::string& val) {
            std::cout << "Evicted: " << key << " = " << val << "\n";
        }
    );

    // RAII guard demo
    auto guard = make_scope_guard([&]{ lru_cache.clear(); });

    // Insert entries
    auto r1 = lru_cache.put("user:1001", "Alice Chen",   30s);
    auto r2 = lru_cache.put("user:1002", "Bob Okafor",   45s);
    auto r3 = lru_cache.put("session:abc", "active",     10s);

    std::cout << "Inserts: "
              << (r1.ok() ? "ok" : r1.error()) << ", "
              << (r2.ok() ? "ok" : r2.error()) << "\n";

    // Read
    if (auto val = lru_cache["user:1001"]) {
        std::cout << "Got: " << *val << "\n";
    }

    // Contains + remove
    std::cout << "Contains user:1002: " << std::boolalpha << lru_cache.contains("user:1002") << "\n";
    lru_cache.remove("user:1002");
    std::cout << "After remove: " << lru_cache.contains("user:1002") << "\n";

    // Snapshot via ranges
    auto snap = lru_cache.snapshot();
    std::cout << "\nCache snapshot (" << snap.size() << " entries):\n";
    for (auto& [k, v] : snap)
        std::cout << "  " << k << " => " << v << "\n";

    // Result chaining
    auto chain = Result<std::string>::Ok("hello")
        .map([](std::string s){ return s.size(); })
        .map([](std::size_t n){ return n * 2; });
    std::cout << "\nResult chain: " << chain.value() << "\n";

    // std::variant visitor
    std::vector<ConfigValue> config = { 42, 3.14, std::string("production"), true };
    std::cout << "\nConfig values:\n";
    for (auto& v : config) std::visit(ConfigPrinter{}, v);

    // constexpr check at runtime print
    std::cout << "\nis_power_of_two(1024): " << std::boolalpha << is_power_of_two(1024) << "\n";
    std::cout << "total_size<int,double>: " << total_size<int, double>() << " bytes\n";

    // Stats
    std::cout << "\nCache stats:\n";
    lru_cache.stats().print();

    // guard fires here — cache cleared
    return 0;
}
