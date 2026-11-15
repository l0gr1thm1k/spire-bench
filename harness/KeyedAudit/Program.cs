// The static half of the keyed harness's audit. The patcher keys MegaRandom and
// guards the randomness sources it knows; this lists every call the game and the
// harness make to anything that could still decide a game without the key: other
// randomness (the clock, Guid, HashCode, Godot's generators, noise seeds) and the
// seed itself, which is public under the key and so must only ever name a keyed
// stream. It reads the patched DLLs and prints one JSON object per call site.
// tests/test_the_keyed_harness_has_no_unreviewed_randomness.py holds the reviewed list.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using Mono.Cecil;
using Mono.Cecil.Cil;

static class Program
{
    const string Guard = "SpireBench.Keyed.KeyedGuard";
    const string Rng = "MegaCrit.Sts2.Core.Random.Rng";

    static int Main(string[] argv)
    {
        if (argv.Length == 0)
        {
            Console.Error.WriteLine("usage: KeyedAudit DLL...   (the keyed build's sts2.dll and Sts2Headless.dll)");
            return 2;
        }
        var sites = new SortedDictionary<string, Site>(StringComparer.Ordinal);
        foreach (var path in argv)
        {
            var resolver = new DefaultAssemblyResolver();
            resolver.AddSearchDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
            resolver.AddSearchDirectory(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(path))!, "lib"));
            using var module = ModuleDefinition.ReadModule(path, new ReaderParameters { AssemblyResolver = resolver });
            foreach (var method in module.GetTypes().SelectMany(t => t.Methods).Where(m => m.HasBody))
                Scan(module.Name, method, sites);
        }
        foreach (var site in sites.Values)
            Console.WriteLine(JsonSerializer.Serialize(site));
        return 0;
    }

    sealed record Site(string assembly, string method, string source, string kind, int calls, bool guarded, bool feeds_rng);

    static void Scan(string assembly, MethodDefinition method, SortedDictionary<string, Site> sites)
    {
        var body = method.Body.Instructions;
        for (int k = 0; k < body.Count; k++)
        {
            if (body[k].Operand is not MethodReference called || Classify(called) is not var (source, kind))
                continue;
            bool guarded = k > 0 && body[k - 1].Operand is MethodReference g && g.DeclaringType.FullName == Guard;
            bool feeds = body.Skip(k + 1).Any(i => i.Operand is MethodReference r && r.Name == ".ctor" && r.DeclaringType.FullName == Rng);
            var where = Owner(method);
            var id = $"{assembly}|{where}|{source}";
            sites[id] = sites.TryGetValue(id, out var seen)
                ? seen with { calls = seen.calls + 1, guarded = seen.guarded && guarded, feeds_rng = seen.feeds_rng && feeds }
                : new Site(assembly, where, source, kind, 1, guarded, feeds);
        }
    }

    /// <summary>The method a reader would name: a lambda or state machine is reported under the method that wrote it.</summary>
    static string Owner(MethodDefinition method)
    {
        var type = method.DeclaringType;
        var name = method.Name;
        while (type.DeclaringType is not null && type.Name.StartsWith('<'))
        {
            var inner = type.Name;
            name = inner.Length > 1 && inner.IndexOf('>') > 1 ? inner[1..inner.IndexOf('>')] : name;
            type = type.DeclaringType;
        }
        if (name.StartsWith('<') && name.IndexOf('>') > 1)
            name = name[1..name.IndexOf('>')];
        return $"{type.FullName}.{name}";
    }

    static (string, string)? Classify(MethodReference m)
    {
        var type = m.DeclaringType.FullName;
        var name = m.Name;
        string? kind = type switch
        {
            "System.Random" or "System.Security.Cryptography.RandomNumberGenerator" or "Godot.RandomNumberGenerator" => "randomness",
            "Godot.GD" when name.StartsWith("Rand") || name == "Seed" => "randomness",
            "System.Guid" when name == "NewGuid" => "randomness",
            "System.HashCode" => "per-process hash",
            "System.String" or "System.Object" when name == "GetHashCode" => "per-process hash",
            "System.DateTime" or "System.DateTimeOffset" when name is "get_Now" or "get_UtcNow" or "get_Today" => "clock",
            "System.Environment" when name.StartsWith("get_TickCount") => "clock",
            "System.Diagnostics.Stopwatch" when name is "GetTimestamp" or "StartNew" or "get_Elapsed" or "get_ElapsedMilliseconds" or "get_ElapsedTicks" => "clock",
            "Godot.Time" when name.StartsWith("GetTicks") || name.StartsWith("GetUnixTime") || name.StartsWith("GetDatetime") => "clock",
            "System.Threading.Tasks.Parallel" => "thread order",
            "MegaCrit.Sts2.Core.Models.EncounterModel" when name == "DebugRandomizeRng" => "randomness",
            "MegaCrit.Sts2.Core.Runs.RunRngSet" when name is "get_Seed" or "get_StringSeed" => "seed",
            "MegaCrit.Sts2.Core.Random.PlayerRngSet" or Rng when name == "get_Seed" => "seed",
            "MegaCrit.Sts2.Core.Helpers.StringHelper" when name == "GetDeterministicHashCode" => "seed",
            "MegaCrit.Sts2.Core.Helpers.SeedHelper" => "seed",
            _ when name.EndsWith("Seed") && name.StartsWith("set_") && type.Contains("Noise") => "randomness",
            _ => null,
        };
        if (kind is null)
            return null;
        var shortType = type[(type.LastIndexOf('.') + 1)..];
        return ($"{shortType}.{name}", kind);
    }
}
