// test_csharp.cs
//
// Holistic C# showcase — smart home automation hub.
//
// Covers: records, primary constructors, interfaces, generics,
// async/await, LINQ, delegates, events, pattern matching, switch
// expressions, init-only properties, required members, nullable
// reference types, extension methods, indexers, operator overloading,
// attributes, reflection, IDisposable, channels, Span<T>, and more.

#nullable enable

using System;
using System.Collections.Generic;
using System.Collections.Concurrent;
using System.ComponentModel;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;

namespace SmartHome;

// ===========================================================================
// Attributes
// ===========================================================================

[AttributeUsage(AttributeTargets.Class | AttributeTargets.Property)]
sealed class DeviceCapabilityAttribute(string capability) : Attribute
{
    public string Capability { get; } = capability;
}

[AttributeUsage(AttributeTargets.Method)]
sealed class CommandAttribute(string name) : Attribute
{
    public string Name { get; } = name;
}

// ===========================================================================
// Enums
// ===========================================================================

enum DeviceState   { Unknown, Online, Offline, Error }
enum DeviceType    { Light, Thermostat, Lock, Camera, Sensor, Speaker }
enum LogLevel      { Debug, Info, Warning, Error, Critical }

[Flags]
enum Permissions
{
    None    = 0,
    Read    = 1 << 0,
    Write   = 1 << 1,
    Control = 1 << 2,
    Admin   = Read | Write | Control,
}

// ===========================================================================
// Records
// ===========================================================================

record DeviceId(string Value)
{
    public static DeviceId New() => new($"dev_{Guid.NewGuid():N}");
    public override string ToString() => Value;
}

record struct Temperature(double Celsius)
{
    public double Fahrenheit => Celsius * 9 / 5 + 32;
    public static Temperature FromFahrenheit(double f) => new((f - 32) * 5 / 9);

    public static Temperature operator +(Temperature a, Temperature b) =>
        new(a.Celsius + b.Celsius);

    public static bool operator <(Temperature a, Temperature b) => a.Celsius < b.Celsius;
    public static bool operator >(Temperature a, Temperature b) => a.Celsius > b.Celsius;

    public override string ToString() => $"{Celsius:F1}°C / {Fahrenheit:F1}°F";
}

record DeviceEvent(
    DeviceId   DeviceId,
    string     EventType,
    object?    Payload,
    DateTimeOffset OccurredAt
)
{
    public static DeviceEvent Create(DeviceId id, string type, object? payload = null) =>
        new(id, type, payload, DateTimeOffset.UtcNow);
}

// ===========================================================================
// Result type
// ===========================================================================

readonly struct Result<T>
{
    private readonly T?        _value;
    private readonly Exception? _error;

    private Result(T value)              { _value = value; _error = null; IsSuccess = true; }
    private Result(Exception error)      { _value = default; _error = error; IsSuccess = false; }

    public bool IsSuccess  { get; }
    public bool IsFailure  => !IsSuccess;

    public T        Value => IsSuccess ? _value! : throw new InvalidOperationException("No value.");
    public Exception Error => IsFailure ? _error! : throw new InvalidOperationException("No error.");

    public static Result<T> Ok(T value)          => new(value);
    public static Result<T> Fail(Exception err)  => new(err);
    public static Result<T> Fail(string msg)     => new(new InvalidOperationException(msg));

    public Result<U> Map<U>(Func<T, U> fn) =>
        IsSuccess ? Result<U>.Ok(fn(Value)) : Result<U>.Fail(Error);

    public override string ToString() =>
        IsSuccess ? $"Ok({_value})" : $"Fail({_error!.Message})";
}

// ===========================================================================
// Interfaces
// ===========================================================================

interface IDevice : INotifyPropertyChanged, IAsyncDisposable
{
    DeviceId   Id          { get; }
    string     Name        { get; }
    DeviceType Type        { get; }
    DeviceState State      { get; }
    DateTimeOffset LastSeen { get; }

    Task<Result<bool>> ConnectAsync(CancellationToken ct = default);
    Task<Result<bool>> DisconnectAsync(CancellationToken ct = default);
    Task<Dictionary<string, object?>> GetStatusAsync(CancellationToken ct = default);
}

interface IControllable
{
    Task<Result<bool>> ExecuteCommandAsync(string command, object? args = null,
        CancellationToken ct = default);
}

interface IObservable<T>
{
    IDisposable Subscribe(Action<T> handler);
}

// ===========================================================================
// Base device
// ===========================================================================

abstract class DeviceBase : IDevice, IObservable<DeviceEvent>
{
    private readonly List<Action<DeviceEvent>> _subscribers = [];
    private DeviceState _state = DeviceState.Unknown;

    protected DeviceBase(DeviceId id, string name, DeviceType type)
    {
        Id   = id;
        Name = name;
        Type = type;
    }

    public DeviceId    Id       { get; }
    public string      Name     { get; }
    public DeviceType  Type     { get; }
    public DateTimeOffset LastSeen { get; protected set; }

    public DeviceState State
    {
        get => _state;
        protected set
        {
            if (_state == value) return;
            var old = _state;
            _state = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(State)));
            Emit(DeviceEvent.Create(Id, "state.changed", new { from = old, to = value }));
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    protected void Emit(DeviceEvent evt) =>
        _subscribers.ForEach(s => s(evt));

    public IDisposable Subscribe(Action<DeviceEvent> handler)
    {
        _subscribers.Add(handler);
        return new Subscription(() => _subscribers.Remove(handler));
    }

    public virtual async Task<Result<bool>> ConnectAsync(CancellationToken ct = default)
    {
        await Task.Delay(Random.Shared.Next(50, 150), ct);
        State    = DeviceState.Online;
        LastSeen = DateTimeOffset.UtcNow;
        return Result<bool>.Ok(true);
    }

    public virtual async Task<Result<bool>> DisconnectAsync(CancellationToken ct = default)
    {
        await Task.Delay(10, ct);
        State = DeviceState.Offline;
        return Result<bool>.Ok(true);
    }

    public abstract Task<Dictionary<string, object?>> GetStatusAsync(CancellationToken ct = default);

    public virtual ValueTask DisposeAsync()
    {
        _subscribers.Clear();
        return ValueTask.CompletedTask;
    }

    private sealed class Subscription(Action onDispose) : IDisposable
    {
        public void Dispose() => onDispose();
    }
}

// ===========================================================================
// Smart light
// ===========================================================================

[DeviceCapability("dimmable")]
[DeviceCapability("colour")]
sealed class SmartLight(DeviceId id, string name) : DeviceBase(id, name, DeviceType.Light), IControllable
{
    private int    _brightness = 100;
    private string _colour     = "#FFFFFF";
    private bool   _isOn       = false;

    public bool   IsOn       { get => _isOn;       private set { _isOn = value;       NotifyStatus(); } }
    public int    Brightness { get => _brightness; private set { _brightness = value; NotifyStatus(); } }
    public string Colour     { get => _colour;     private set { _colour = value;     NotifyStatus(); } }

    [Command("turn_on")]
    public Task TurnOnAsync()  { IsOn = true;  return Task.CompletedTask; }

    [Command("turn_off")]
    public Task TurnOffAsync() { IsOn = false; return Task.CompletedTask; }

    [Command("dim")]
    public Task DimAsync(int pct)
    {
        Brightness = Math.Clamp(pct, 0, 100);
        return Task.CompletedTask;
    }

    public async Task<Result<bool>> ExecuteCommandAsync(
        string command, object? args = null, CancellationToken ct = default)
    {
        await Task.Delay(20, ct);
        return command switch
        {
            "turn_on"  => await TurnOnAsync().ContinueWith(_ => Result<bool>.Ok(true), ct),
            "turn_off" => await TurnOffAsync().ContinueWith(_ => Result<bool>.Ok(true), ct),
            "dim"      when args is int pct => await DimAsync(pct)
                                               .ContinueWith(_ => Result<bool>.Ok(true), ct),
            _          => Result<bool>.Fail($"Unknown command: {command}"),
        };
    }

    public override async Task<Dictionary<string, object?>> GetStatusAsync(CancellationToken ct = default)
    {
        await Task.Delay(5, ct);
        return new()
        {
            ["is_on"]      = IsOn,
            ["brightness"] = Brightness,
            ["colour"]     = Colour,
        };
    }

    private void NotifyStatus() =>
        Emit(DeviceEvent.Create(Id, "status.updated",
            new { IsOn, Brightness, Colour }));
}

// ===========================================================================
// Thermostat
// ===========================================================================

sealed class Thermostat(DeviceId id, string name) : DeviceBase(id, name, DeviceType.Thermostat)
{
    private Temperature _setpoint    = new(21);
    private Temperature _current     = new(19);
    private bool        _isHeating   = false;
    private readonly List<(DateTimeOffset At, Temperature Temp)> _history = [];

    public Temperature Setpoint  { get => _setpoint; }
    public Temperature Current   { get => _current;  }
    public bool        IsHeating => _isHeating;

    public Result<bool> SetTemperature(Temperature target)
    {
        if (target.Celsius is < 5 or > 35)
            return Result<bool>.Fail("Setpoint must be between 5°C and 35°C.");

        _setpoint  = target;
        _isHeating = _current < _setpoint;
        _history.Add((DateTimeOffset.UtcNow, target));
        Emit(DeviceEvent.Create(Id, "setpoint.changed", new { Setpoint = target.Celsius }));
        return Result<bool>.Ok(true);
    }

    public void SimulateReading(Temperature measured)
    {
        _current   = measured;
        _isHeating = _current < _setpoint;
        Emit(DeviceEvent.Create(Id, "temperature.reading",
            new { Celsius = measured.Celsius, IsHeating = _isHeating }));
    }

    public IReadOnlyList<(DateTimeOffset At, Temperature Temp)> History => _history;

    public override async Task<Dictionary<string, object?>> GetStatusAsync(CancellationToken ct = default)
    {
        await Task.Delay(5, ct);
        return new()
        {
            ["setpoint_c"] = Setpoint.Celsius,
            ["current_c"]  = Current.Celsius,
            ["is_heating"] = IsHeating,
        };
    }
}

// ===========================================================================
// Device hub — generic collection with indexer
// ===========================================================================

sealed class DeviceHub : IAsyncDisposable
{
    private readonly ConcurrentDictionary<DeviceId, IDevice> _devices = new();
    private readonly Channel<DeviceEvent>                     _eventBus;
    private readonly CancellationTokenSource                  _cts = new();
    private readonly Task                                     _processTask;
    private readonly List<Action<DeviceEvent>>                _globalHandlers = [];

    public DeviceHub(int eventBusCapacity = 1000)
    {
        _eventBus    = Channel.CreateBounded<DeviceEvent>(
            new BoundedChannelOptions(eventBusCapacity) { FullMode = BoundedChannelFullMode.DropOldest }
        );
        _processTask = ProcessEventsAsync(_cts.Token);
    }

    // Indexer
    public IDevice? this[DeviceId id] => _devices.TryGetValue(id, out var d) ? d : null;

    public void Register(IDevice device)
    {
        _devices[device.Id] = device;
        if (device is IObservable<DeviceEvent> obs)
            obs.Subscribe(evt => _eventBus.Writer.TryWrite(evt));
    }

    public void OnEvent(Action<DeviceEvent> handler) => _globalHandlers.Add(handler);

    public async Task ConnectAllAsync(CancellationToken ct = default)
    {
        var tasks = _devices.Values.Select(d => d.ConnectAsync(ct));
        await Task.WhenAll(tasks);
    }

    public async Task<Dictionary<DeviceId, Dictionary<string, object?>>> PollAllAsync(
        CancellationToken ct = default)
    {
        var tasks = _devices.ToDictionary(
            kv => kv.Key,
            kv => kv.Value.GetStatusAsync(ct)
        );
        await Task.WhenAll(tasks.Values);
        return tasks.ToDictionary(kv => kv.Key, kv => kv.Value.Result);
    }

    // LINQ queries over devices
    public IEnumerable<IDevice> GetByType(DeviceType type) =>
        _devices.Values.Where(d => d.Type == type);

    public IEnumerable<IDevice> GetOnline() =>
        _devices.Values.Where(d => d.State == DeviceState.Online);

    public ILookup<DeviceType, IDevice> GroupByType() =>
        _devices.Values.ToLookup(d => d.Type);

    private async Task ProcessEventsAsync(CancellationToken ct)
    {
        await foreach (var evt in _eventBus.Reader.ReadAllAsync(ct))
        {
            _globalHandlers.ForEach(h => h(evt));
        }
    }

    public async ValueTask DisposeAsync()
    {
        _cts.Cancel();
        _eventBus.Writer.Complete();
        await _processTask.ConfigureAwait(false);
        foreach (var device in _devices.Values)
            await device.DisposeAsync();
        _cts.Dispose();
    }
}

// ===========================================================================
// Extension methods
// ===========================================================================

static class DeviceExtensions
{
    public static async Task<Result<bool>> SafeExecuteAsync(
        this IControllable device, string cmd, object? args = null,
        CancellationToken ct = default)
    {
        try
        {
            return await device.ExecuteCommandAsync(cmd, args, ct);
        }
        catch (OperationCanceledException)
        {
            return Result<bool>.Fail("Operation cancelled.");
        }
        catch (Exception ex)
        {
            return Result<bool>.Fail(ex);
        }
    }

    public static string DescribeCapabilities(this IDevice device)
    {
        var attrs = device.GetType()
            .GetCustomAttributes<DeviceCapabilityAttribute>()
            .Select(a => a.Capability);
        return string.Join(", ", attrs);
    }

    public static IEnumerable<string> GetCommands(this IDevice device) =>
        device.GetType()
            .GetMethods()
            .SelectMany(m => m.GetCustomAttributes<CommandAttribute>())
            .Select(a => a.Name);
}

// ===========================================================================
// Main
// ===========================================================================

class Program
{
    static async Task Main()
    {
        Console.WriteLine("Smart Home Hub Demo\n");

        await using var hub = new DeviceHub();

        var light1 = new SmartLight(DeviceId.New(), "Living Room Light");
        var light2 = new SmartLight(DeviceId.New(), "Bedroom Light");
        var thermo  = new Thermostat(DeviceId.New(), "Main Thermostat");

        hub.Register(light1);
        hub.Register(light2);
        hub.Register(thermo);

        hub.OnEvent(evt =>
            Console.WriteLine($"[EVENT] {evt.DeviceId} → {evt.EventType}"));

        await hub.ConnectAllAsync();
        Console.WriteLine($"Connected {hub.GetOnline().Count()} devices\n");

        // Pattern matching and switch expression
        foreach (var device in hub.GetOnline())
        {
            var desc = device switch
            {
                SmartLight l  => $"Light — brightness {l.Brightness}%",
                Thermostat t  => $"Thermostat — setpoint {t.Setpoint}",
                _             => $"Unknown device type",
            };
            Console.WriteLine($"  {device.Name}: {desc}");
        }

        // Commands
        await light1.TurnOnAsync();
        await light1.DimAsync(75);
        var r = await ((IControllable)light1).SafeExecuteAsync("dim", 50);
        Console.WriteLine($"\nDim result: {r}");

        // Thermostat
        var setResult = thermo.SetTemperature(new Temperature(22));
        thermo.SimulateReading(new Temperature(18));
        Console.WriteLine($"Thermostat: {thermo.Current} → target {thermo.Setpoint}, heating={thermo.IsHeating}");

        // Temperature arithmetic (operator overloading)
        var delta = new Temperature(22) + new Temperature(3);
        Console.WriteLine($"Temperature math: {delta}");

        // Reflection — capabilities
        Console.WriteLine($"\nLight1 capabilities: {light1.DescribeCapabilities()}");
        Console.WriteLine($"Light1 commands    : {string.Join(", ", light1.GetCommands())}");

        // Poll all
        var statuses = await hub.PollAllAsync();
        Console.WriteLine($"\nPolled {statuses.Count} devices:");
        foreach (var (id, status) in statuses)
        {
            var json = JsonSerializer.Serialize(status);
            Console.WriteLine($"  {id}: {json}");
        }

        // LINQ grouping
        var byType = hub.GroupByType();
        foreach (var group in byType)
            Console.WriteLine($"\n{group.Key} ({group.Count()} device(s)):");

        // Span<T> demo — process temperature history in stack memory
        var readings = thermo.History;
        if (readings.Count > 0)
        {
            Span<double> temps = stackalloc double[readings.Count];
            for (int i = 0; i < readings.Count; i++)
                temps[i] = readings[i].Temp.Celsius;
            var avg = 0.0;
            foreach (var t in temps) avg += t;
            Console.WriteLine($"\nAvg setpoint history: {avg / temps.Length:F1}°C");
        }

        // Result chaining
        var chain = Result<int>.Ok(22)
            .Map(c => new Temperature(c))
            .Map(t => t.Fahrenheit);
        Console.WriteLine($"\nResult chain: {chain}");

        await Task.Delay(200);
        Console.WriteLine("\nHub shutting down...");
    }
}
