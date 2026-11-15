using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;

namespace SpireBench.Keyed;

/// <summary>A draw the episode key does not decide, made while a keyed game is running.</summary>
public sealed class UnkeyedDrawException : InvalidOperationException
{
    public UnkeyedDrawException(string what) : base($"unkeyed draw refused: {what}") { }
}

/// <summary>
/// Every generator the game opens draws from ChaCha20 under a key of its own,
/// HMAC-SHA256(episode key, label | the game's 64-bit seed for it). The game's
/// seed derivation names the stream, so no list of streams is kept here.
/// A process plays one keyed game: once its key is installed it never draws
/// unkeyed again and never takes another key.
/// </summary>
public static class KeyedStreams
{
    /// <summary>What the game sees as its seed string: public, the same for every game.</summary>
    public const string PublicSeed = "SPIREBENCH";

    static readonly byte[] Label = Encoding.ASCII.GetBytes("spire-bench stream v1\0");
    static readonly byte[] ChaoticLabel = Encoding.ASCII.GetBytes("spire-bench chaotic v1");
    static readonly object Gate = new();
    static byte[]? _key;
    static object? _chaotic;

    static KeyedStreams() => ChaCha20.SelfTest();

    /// <summary>Install the key of the game about to start; returns the seed string the game is given.</summary>
    public static string Begin(string? seed)
    {
        if (seed is null || seed.Length != 64 || !IsHex(seed))
            throw new ArgumentException("seed string refused: the keyed harness takes a 32-byte episode key as 64 hex digits");
        lock (Gate)
        {
            if (_key is not null)
                throw new InvalidOperationException("a keyed harness plays one game: start a new process for the next key");
            _key = Convert.FromHexString(seed);
        }
        return PublicSeed;
    }

    /// <summary>The stream a generator seeded with <paramref name="seed"/> draws from; null while no key was ever installed.</summary>
    public static object? Open(ulong seed)
    {
        lock (Gate)
        {
            if (_key is null)
                return null;
            var message = new byte[Label.Length + 8];
            Label.CopyTo(message, 0);
            BitConverter.TryWriteBytes(message.AsSpan(Label.Length), seed);
            if (!BitConverter.IsLittleEndian)
                Array.Reverse(message, Label.Length, 8);
            return new Stream(HMACSHA256.HashData(_key, message));
        }
    }

    /// <summary>
    /// Rng.Chaotic is the game's clock-seeded generator (cosmetics, but also text
    /// variants and the canonical monsters' draws). Under the key it is one keyed Rng
    /// on a stream of its own label; this is it, or null until it is made.
    /// </summary>
    public static object? CurrentChaotic()
    {
        lock (Gate)
            return _chaotic;
    }

    public static object OpenChaotic()
    {
        lock (Gate)
            return new Stream(HMACSHA256.HashData(_key ?? throw new UnkeyedDrawException("Rng.Chaotic opened with no key"), ChaoticLabel));
    }

    /// <summary>Keeps the first Rng made as Rng.Chaotic and returns the one kept.</summary>
    public static object SetChaotic(object rng)
    {
        lock (Gate)
            return _chaotic ??= rng;
    }

    /// <summary>True when the generator owning <paramref name="stream"/> must draw from it; throws on a draw the key does not decide.</summary>
    public static bool Keyed(object? stream)
    {
        if (stream is Stream)
            return true;
        if (Volatile.Read(ref _key) is not null)
            throw new UnkeyedDrawException("a generator opened before any key drew in a keyed game");
        return false;
    }

    /// <summary>
    /// The trace build only (build_keyed.sh --trace-streams, for tools/fuzz_keyed.py): one stderr
    /// line per stream opened under the key, naming its seed, who opened it, and its first draw.
    /// </summary>
    public static void TraceOpen(ulong seed)
    {
        if (Open(seed) is not Stream stream)
            return;
        var by = new List<string>();
        foreach (var frame in new System.Diagnostics.StackTrace(1, false).GetFrames())
        {
            var m = frame.GetMethod();
            if (m?.DeclaringType is null)
                continue;
            by.Add($"{m.DeclaringType.FullName}.{m.Name}");
            if (by.Count >= 6 || !m.DeclaringType.FullName!.StartsWith("MegaCrit.Sts2.Core.Random."))
                break;
        }
        var line = JsonSerializer.Serialize(new { spirebench_trace = "open", seed, by, first = stream.Next() });
        Console.Error.WriteLine(line);
    }

    public static bool KeyActive => Volatile.Read(ref _key) is not null;

    public static ulong Next(object stream) => ((Stream)stream).Next();

    /// <summary>MegaRandom.NextBytes writes its state directly; under a key it is refused rather than left unkeyed.</summary>
    public static void RefuseBytes(object? stream)
    {
        if (Keyed(stream))
            throw new UnkeyedDrawException("MegaRandom.NextBytes is not keyed");
    }

    static bool IsHex(string s)
    {
        foreach (var c in s)
            if (!Uri.IsHexDigit(c))
                return false;
        return true;
    }

    sealed class Stream
    {
        static readonly byte[] ZeroNonce = new byte[12];
        readonly byte[] _key;
        readonly uint[] _block = new uint[16];
        uint _counter;
        int _word = 16;

        public Stream(byte[] key) => _key = key;

        /// <summary>Draw n is the n-th little-endian 64-bit word of the keystream.</summary>
        public ulong Next()
        {
            if (_word == 16)
            {
                ChaCha20.Block(_key, _counter, ZeroNonce, _block);
                _counter = checked(_counter + 1);
                _word = 0;
            }
            ulong lo = _block[_word], hi = _block[_word + 1];
            _word += 2;
            return lo | (hi << 32);
        }
    }
}

/// <summary>Called before every other source of randomness in the game and the harness (System.Random, Godot's GD.Rand*, the dev console's clock-seeded encounters).</summary>
public static class KeyedGuard
{
    public static void Unkeyed(string source)
    {
        if (KeyedStreams.KeyActive)
            throw new UnkeyedDrawException(source);
    }

    /// <summary>
    /// The harness's commands the gateway sends, with the fields it sends. Every other
    /// command (rng_probe, roll_acts, get_state, set_draw_order, dump_*, saves...) can read
    /// or set what the key decides, so the keyed build refuses it before it runs.
    /// </summary>
    static readonly Dictionary<string, string[]> Allowed = new()
    {
        ["start_run"] = new[] { "character", "ascension", "seed", "act1", "interactive_potion_swap" },
        ["action"] = new[] { "action", "args" },
        ["legal_actions"] = Array.Empty<string>(),
        ["quit"] = Array.Empty<string>(),
    };

    public static void Command(JsonElement cmd)
    {
        var name = cmd.ValueKind == JsonValueKind.Object && cmd.TryGetProperty("cmd", out var c) && c.ValueKind == JsonValueKind.String
            ? c.GetString()! : "";
        if (!Allowed.TryGetValue(name, out var fields))
            throw new CommandRefusedException($"command {(name == "" ? "(none)" : name)}");
        foreach (var prop in cmd.EnumerateObject())
            if (prop.Name != "cmd" && Array.IndexOf(fields, prop.Name) < 0)
                throw new CommandRefusedException($"field {prop.Name} of {name}");
    }
}

/// <summary>A harness command, or a field of one, the keyed build does not take.</summary>
public sealed class CommandRefusedException : InvalidOperationException
{
    public CommandRefusedException(string what) : base($"refused by the keyed harness: {what}") { }
}
