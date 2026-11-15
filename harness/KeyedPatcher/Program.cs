// Copies a built Sts2Headless and its game DLLs into OUT and rewrites them so
// every game draw comes from the episode key (see harness/KeyedRng):
//   MegaRandom.Reinitialise(seed) opens the keyed stream for that seed,
//   MegaRandom.NextULongInner (the one method every draw goes through) draws from it,
//   MegaRandom.NextBytes, and restoring a MegaRandom from a save (v0.111.0+), are refused under a key,
//   Rng.Chaotic, the game's clock-seeded generator, is a keyed one per game,
//   every call to another source of randomness first asks KeyedGuard.Unkeyed,
//   RunSimulator.StartRun takes the key where it took a seed string.
// --trace-streams (a separate build, for tools/fuzz_keyed.py) also logs each stream opened.
// --fuzz-immortal (tools/fuzz_keyed.py --deep only) keeps the player at 1 HP or more; with --seeded it
// patches nothing else, so the leak check has a seeded game that lives as long.
// Each rewrite must apply exactly as counted, or nothing is written.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;
using Mono.Cecil.Rocks;

static class Program
{
    const string MegaRandom = "MegaCrit.Sts2.Core.Random.MegaRandom";
    const string Rng = "MegaCrit.Sts2.Core.Random.Rng";
    const string RunSimulator = "Sts2Headless.RunSimulator";
    const string SerializableRng = "MegaCrit.Sts2.Core.Saves.SerializableRng";
    const string Creature = "MegaCrit.Sts2.Core.Entities.Creatures.Creature";

    static int Main(string[] argv)
    {
        var opts = Options.Parse(argv);
        if (opts is null)
        {
            Console.Error.WriteLine("usage: KeyedPatcher --bin DIR --lib DIR --keyedrng DLL --localization DIR --out DIR [--plant-unkeyed-draw] [--trace-streams] [--fuzz-immortal [--seeded]]");
            return 2;
        }
        var outLib = Path.Combine(opts.Out, "lib");
        if (Directory.Exists(opts.Out))
            Directory.Delete(opts.Out, recursive: true);
        CopyDir(opts.Bin, opts.Out);
        CopyDir(opts.Lib, outLib, skip: name => name.EndsWith(".original"));
        File.Copy(opts.KeyedRng, Path.Combine(outLib, "KeyedRng.dll"));
        Directory.CreateSymbolicLink(Path.Combine(outLib, "localization_eng"), Path.GetFullPath(opts.Localization));

        var resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(outLib);
        resolver.AddSearchDirectory(opts.Out);
        var keyed = ModuleDefinition.ReadModule(Path.Combine(outLib, "KeyedRng.dll"));

        var game = Path.Combine(outLib, "sts2.dll");
        if (opts.Seeded)
        {
            Rewrite(game, resolver, module => FuzzImmortal(module, new Api(module, keyed)));
            Console.WriteLine($"seeded fuzz harness written to {opts.Out}");
            return 0;
        }
        Rewrite(game, resolver, module =>
        {
            var api = new Api(module, keyed);
            if (opts.FuzzImmortal)
                FuzzImmortal(module, api);
            var stream = KeyMegaRandom(module, api);
            KeyChaotic(module, api, stream);
            if (opts.TraceStreams)
                TraceStreams(module, api);
            Console.WriteLine($"  guarded {GuardUnkeyedSources(module, api)} other randomness call sites in sts2.dll");
        });
        Rewrite(Path.Combine(opts.Out, "Sts2Headless.dll"), resolver, module =>
        {
            var api = new Api(module, keyed);
            TakeKeyAtStartRun(module, api);
            RefuseDebugCommands(module, api);
            if (opts.PlantUnkeyedDraw)
                PlantUnkeyedDraw(module);
            Console.WriteLine($"  guarded {GuardUnkeyedSources(module, api)} other randomness call sites in Sts2Headless.dll");
        });
        Console.WriteLine($"keyed harness written to {opts.Out}");
        return 0;
    }

    sealed record Options(string Bin, string Lib, string KeyedRng, string Localization, string Out, bool PlantUnkeyedDraw, bool TraceStreams,
                          bool FuzzImmortal, bool Seeded)
    {
        public static Options? Parse(string[] argv)
        {
            var named = new Dictionary<string, string>();
            bool plant = false, trace = false, immortal = false, seeded = false;
            for (int i = 0; i < argv.Length; i++)
            {
                if (argv[i] == "--plant-unkeyed-draw") plant = true;
                else if (argv[i] == "--trace-streams") trace = true;
                else if (argv[i] == "--fuzz-immortal") immortal = true;
                else if (argv[i] == "--seeded") seeded = true;
                else if (argv[i].StartsWith("--") && i + 1 < argv.Length) named[argv[i]] = argv[++i];
                else return null;
            }
            string[] need = { "--bin", "--lib", "--keyedrng", "--localization", "--out" };
            if (need.Any(k => !named.ContainsKey(k)) || (seeded && (!immortal || plant || trace)))
                return null;
            return new Options(named["--bin"], named["--lib"], named["--keyedrng"], named["--localization"], named["--out"], plant, trace,
                               immortal, seeded);
        }
    }

    /// <summary>KeyedRng's entry points, imported into the module being rewritten.</summary>
    sealed class Api
    {
        public Api(ModuleDefinition module, ModuleDefinition keyed)
        {
            MethodReference Get(string type, string name) =>
                module.ImportReference(keyed.GetType(type).Methods.Single(m => m.Name == name));
            Open = Get("SpireBench.Keyed.KeyedStreams", "Open");
            Keyed = Get("SpireBench.Keyed.KeyedStreams", "Keyed");
            Next = Get("SpireBench.Keyed.KeyedStreams", "Next");
            RefuseBytes = Get("SpireBench.Keyed.KeyedStreams", "RefuseBytes");
            RefuseRestore = Get("SpireBench.Keyed.KeyedStreams", "RefuseRestore");
            Begin = Get("SpireBench.Keyed.KeyedStreams", "Begin");
            KeyActive = Get("SpireBench.Keyed.KeyedStreams", "get_KeyActive");
            CurrentChaotic = Get("SpireBench.Keyed.KeyedStreams", "CurrentChaotic");
            OpenChaotic = Get("SpireBench.Keyed.KeyedStreams", "OpenChaotic");
            SetChaotic = Get("SpireBench.Keyed.KeyedStreams", "SetChaotic");
            Unkeyed = Get("SpireBench.Keyed.KeyedGuard", "Unkeyed");
            Command = Get("SpireBench.Keyed.KeyedGuard", "Command");
            TraceOpen = Get("SpireBench.Keyed.KeyedStreams", "TraceOpen");
            KeepPlayerAlive = Get("SpireBench.Keyed.FuzzOnly", "KeepPlayerAlive");
            KeepPlayerHp = Get("SpireBench.Keyed.FuzzOnly", "KeepPlayerHp");
            RunEnding = Get("SpireBench.Keyed.FuzzOnly", "RunEnding");
        }

        public MethodReference KeepPlayerAlive, KeepPlayerHp, RunEnding, TraceOpen, Open, Keyed, Next, RefuseBytes, RefuseRestore, Begin, Unkeyed, Command, KeyActive, CurrentChaotic, OpenChaotic, SetChaotic;
    }

    static FieldDefinition KeyMegaRandom(ModuleDefinition module, Api api)
    {
        var type = module.GetType(MegaRandom) ?? throw new InvalidOperationException($"{MegaRandom} not found");
        var stream = new FieldDefinition("__keyedStream", FieldAttributes.Assembly, module.TypeSystem.Object);
        type.Fields.Add(stream);

        var reinit = SeededReinitialise(type);
        Prepend(reinit, il => new[]
        {
            il.Create(OpCodes.Ldarg_0), il.Create(OpCodes.Ldarg_1), il.Create(OpCodes.Call, api.Open),
            il.Create(OpCodes.Stfld, stream),
        });

        var inner = Only(type, "NextULongInner");
        var original = inner.Body.Instructions[0];
        Prepend(inner, il => new[]
        {
            il.Create(OpCodes.Ldarg_0), il.Create(OpCodes.Ldfld, stream), il.Create(OpCodes.Call, api.Keyed),
            il.Create(OpCodes.Brfalse, original),
            il.Create(OpCodes.Ldarg_0), il.Create(OpCodes.Ldfld, stream), il.Create(OpCodes.Call, api.Next),
            il.Create(OpCodes.Ret),
        });

        Prepend(Only(type, "NextBytes"), il => new[]
        {
            il.Create(OpCodes.Ldarg_0), il.Create(OpCodes.Ldfld, stream), il.Create(OpCodes.Call, api.RefuseBytes),
        });

        var reviewed = new List<MethodDefinition> { reinit, inner, Only(type, "NextBytes") };
        foreach (var restore in type.Methods.Where(m => m.Name == "Reinitialise" && m != reinit))
        {
            if (restore.Parameters.Count != 1 || restore.Parameters[0].ParameterType.FullName != SerializableRng)
                throw new InvalidOperationException($"unreviewed {restore.FullName}; this game build needs a new review");
            Prepend(restore, il => new[] { il.Create(OpCodes.Call, api.RefuseRestore) });
            reviewed.Add(restore);
        }
        // Rng.ToSerializable (the harness's per-view rng counters) copies the state out. Under a key NextULongInner never
        // advances it, so it holds what the public seed set; it must only read it.
        if (type.Methods.SingleOrDefault(m => m.Name == "FillSerializableState") is { } fill)
        {
            if (fill.Body.Instructions.Any(i => i.OpCode.Code == Code.Stfld && IsState(i.Operand)))
                throw new InvalidOperationException("MegaRandom.FillSerializableState writes the state; this game build needs a new review");
            reviewed.Add(fill);
        }

        var touching = type.Methods.Where(m => m.HasBody && m.Body.Instructions.Any(i => IsState(i.Operand)))
            .Select(m => m.FullName).OrderBy(n => n, StringComparer.Ordinal).ToList();
        var expected = reviewed.Select(m => m.FullName).OrderBy(n => n, StringComparer.Ordinal).ToList();
        if (!touching.SequenceEqual(expected))
            throw new InvalidOperationException($"MegaRandom's state is touched by {string.Join(", ", touching)}, not only {string.Join(", ", expected)}; this game build needs a new review");
        Console.WriteLine($"  keyed MegaRandom: {string.Join(", ", reviewed.Select(m => $"{m.Name}({string.Join(",", m.Parameters.Select(p => p.ParameterType.Name))})"))}");
        return stream;
    }

    static bool IsState(object? operand) =>
        operand is FieldReference f && f.DeclaringType.FullName == MegaRandom && f.Name is "_s0" or "_s1" or "_s2" or "_s3";

    /// <summary>Reinitialise(ulong seed): every generator the game seeds goes through it.</summary>
    static MethodDefinition SeededReinitialise(TypeDefinition type) =>
        type.Methods.Single(m => m.Name == "Reinitialise" && m.HasBody && m.Parameters.Count == 1
                                 && m.Parameters[0].ParameterType.MetadataType == MetadataType.UInt64);

    /// <summary>Under a key, Rng.Chaotic returns this game's own Rng, made on first use on the chaotic stream.</summary>
    static void KeyChaotic(ModuleDefinition module, Api api, FieldDefinition stream)
    {
        var rng = module.GetType(Rng) ?? throw new InvalidOperationException($"{Rng} not found");
        var getter = Only(rng, "get_Chaotic");
        var (ctor, seedArgs) = SeededRngCtor(rng);
        var random = rng.Fields.Single(f => f.Name == "_random" && f.FieldType.FullName == MegaRandom);
        var original = getter.Body.Instructions[0];
        Prepend(getter, il =>
        {
            var cast = il.Create(OpCodes.Castclass, rng);
            var code = new List<Instruction>
            {
                il.Create(OpCodes.Call, api.KeyActive), il.Create(OpCodes.Brfalse, original),
                il.Create(OpCodes.Call, api.CurrentChaotic), il.Create(OpCodes.Dup), il.Create(OpCodes.Brtrue, cast),
                il.Create(OpCodes.Pop),
            };
            code.AddRange(seedArgs(il));
            code.AddRange(new[]
            {
                il.Create(OpCodes.Newobj, ctor),
                il.Create(OpCodes.Dup), il.Create(OpCodes.Ldfld, random), il.Create(OpCodes.Call, api.OpenChaotic),
                il.Create(OpCodes.Stfld, stream),
                il.Create(OpCodes.Call, api.SetChaotic),
                cast, il.Create(OpCodes.Ret),
            });
            return code.ToArray();
        });
        Console.WriteLine($"  keyed Rng.Chaotic through {ctor.FullName}");
    }

    /// <summary>The Rng constructor Chaotic is built with, and its zero seed: (UInt32 seed, Int32 counter) to v0.107.1, (UInt64 seed) from v0.111.0.</summary>
    static (MethodDefinition, Func<ILProcessor, Instruction[]>) SeededRngCtor(TypeDefinition rng)
    {
        bool Shape(MethodDefinition m, params MetadataType[] types) => m.IsConstructor && !m.IsStatic
            && m.Parameters.Select(p => p.ParameterType.MetadataType).SequenceEqual(types);
        var ctors = rng.Methods.Where(m => m.IsConstructor && !m.IsStatic).ToList();
        var old = ctors.SingleOrDefault(m => Shape(m, MetadataType.UInt32, MetadataType.Int32));
        if (old is not null)
            return (old, il => new[] { il.Create(OpCodes.Ldc_I4_0), il.Create(OpCodes.Ldc_I4_0) });
        var u64 = ctors.SingleOrDefault(m => Shape(m, MetadataType.UInt64));
        if (u64 is not null)
            return (u64, il => new[] { il.Create(OpCodes.Ldc_I8, 0L) });
        throw new InvalidOperationException("unreviewed Rng constructors: "
            + string.Join(", ", ctors.Select(m => m.FullName)));
    }

    /// <summary>The trace build: MegaRandom.Reinitialise first logs the stream it is about to open.</summary>
    static void TraceStreams(ModuleDefinition module, Api api)
    {
        var reinit = SeededReinitialise(module.GetType(MegaRandom));
        Prepend(reinit, il => new[] { il.Create(OpCodes.Ldarg_1), il.Create(OpCodes.Call, api.TraceOpen) });
        Console.WriteLine("  TRACING every stream opened (trace build, never the gateway's)");
    }

    /// <summary>The fuzz-deep build: Creature.LoseHpInternal and set_CurrentHp go through FuzzOnly, which keeps the player at 1 HP or more.</summary>
    static void FuzzImmortal(ModuleDefinition module, Api api)
    {
        var creature = module.GetType(Creature) ?? throw new InvalidOperationException($"{Creature} not found");
        var currentHp = Only(creature, "get_CurrentHp");
        var isPlayer = Only(creature, "get_IsPlayer");
        var lose = Only(creature, "LoseHpInternal");
        var set = Only(creature, "set_CurrentHp");
        if (lose.Parameters.Count != 2 || lose.Parameters[0].ParameterType.FullName != "System.Decimal"
            || set.Parameters.Count != 1 || set.Parameters[0].ParameterType.MetadataType != MetadataType.Int32)
            throw new InvalidOperationException($"unreviewed {lose.FullName} or {set.FullName}; this game build needs a new review");
        foreach (var (method, keep) in new[] { (lose, api.KeepPlayerAlive), (set, api.KeepPlayerHp) })
            Prepend(method, il => new[]
            {
                il.Create(OpCodes.Ldarg_1),
                il.Create(OpCodes.Ldarg_0), il.Create(OpCodes.Call, currentHp),
                il.Create(OpCodes.Ldarg_0), il.Create(OpCodes.Call, isPlayer),
                il.Create(OpCodes.Call, keep), il.Create(OpCodes.Starg, method.Parameters[0]),
            });
        var runs = module.GetType("MegaCrit.Sts2.Core.Runs.RunManager") ?? throw new InvalidOperationException("RunManager not found");
        var killAll = Only(runs, "GuaranteeKillAllPlayers");
        if (killAll.Parameters.Count != 0)
            throw new InvalidOperationException($"unreviewed {killAll.FullName}; this game build needs a new review");
        Prepend(killAll, il => new[] { il.Create(OpCodes.Call, api.RunEnding) });
        Console.WriteLine("  IMMORTAL player: Creature.LoseHpInternal and set_CurrentHp stop at 1 HP, then enemies die on a hit (the player dies after FuzzOnly.SavesPerKill saves without a kill);"
                          + " RunManager.GuaranteeKillAllPlayers lifts it so a won run ends (fuzz-deep build, never the gateway's)");
    }

    static void TakeKeyAtStartRun(ModuleDefinition module, Api api)
    {
        var sim = module.GetType(RunSimulator) ?? throw new InvalidOperationException($"{RunSimulator} not found");
        var start = Only(sim, "StartRun");
        var seed = start.Parameters.Single(p => p.Name == "seed");
        Prepend(start, il => new[]
        {
            il.Create(OpCodes.Ldarg, seed), il.Create(OpCodes.Call, api.Begin), il.Create(OpCodes.Starg, seed),
        });
        Console.WriteLine("  RunSimulator.StartRun takes the episode key");
    }

    /// <summary>HandleCommand first asks KeyedGuard.Command, which refuses every command the gateway does not send.</summary>
    static void RefuseDebugCommands(ModuleDefinition module, Api api)
    {
        var program = module.GetType("Sts2Headless.Program") ?? throw new InvalidOperationException("Sts2Headless.Program not found");
        var handle = Only(program, "HandleCommand");
        var cmd = handle.Parameters.Single(p => p.ParameterType.FullName == "System.Text.Json.JsonElement");
        Prepend(handle, il => new[] { il.Create(OpCodes.Ldarg, cmd), il.Create(OpCodes.Call, api.Command) });
        Console.WriteLine("  Program.HandleCommand refuses every command the gateway does not send");
    }

    /// <summary>For the test that an unkeyed draw voids the game: StartRunImpl first draws from System.Random.</summary>
    static void PlantUnkeyedDraw(ModuleDefinition module)
    {
        var impl = Only(module.GetType(RunSimulator), "StartRunImpl");
        var ctor = module.ImportReference(typeof(System.Random).GetConstructor(Type.EmptyTypes)!);
        var next = module.ImportReference(typeof(System.Random).GetMethod("Next", Type.EmptyTypes)!);
        Prepend(impl, il => new[] { il.Create(OpCodes.Newobj, ctor), il.Create(OpCodes.Callvirt, next), il.Create(OpCodes.Pop) });
        Console.WriteLine("  PLANTED an unkeyed System.Random draw in RunSimulator.StartRunImpl (test build)");
    }

    static string? UnkeyedSource(MethodReference m)
    {
        var type = m.DeclaringType.FullName;
        return type switch
        {
            "System.Random" when m.Name is ".ctor" or "get_Shared" => $"System.Random.{m.Name}",
            "MegaCrit.Sts2.Core.Models.EncounterModel" when m.Name == "DebugRandomizeRng" => "EncounterModel.DebugRandomizeRng",
            "Godot.GD" when m.Name.StartsWith("Rand") || m.Name == "Seed" => $"GD.{m.Name}",
            "System.Security.Cryptography.RandomNumberGenerator" => $"RandomNumberGenerator.{m.Name}",
            _ => null,
        };
    }

    /// <summary>Puts KeyedGuard.Unkeyed(source) before each call to another source of randomness.</summary>
    static int GuardUnkeyedSources(ModuleDefinition module, Api api)
    {
        int sites = 0;
        foreach (var method in module.GetTypes().SelectMany(t => t.Methods).Where(m => m.HasBody))
        {
            var body = method.Body;
            var hits = body.Instructions.Where(i => i.Operand is MethodReference r && UnkeyedSource(r) is not null).ToList();
            if (hits.Count == 0)
                continue;
            body.SimplifyMacros();
            var il = body.GetILProcessor();
            foreach (var call in hits)
            {
                // The call instruction becomes the guard's ldstr, so every branch and handler that targeted the call now runs the guard first.
                var copy = il.Create(call.OpCode, (MethodReference)call.Operand);
                var source = UnkeyedSource((MethodReference)call.Operand)!;
                call.OpCode = OpCodes.Ldstr;
                call.Operand = source;
                var guard = il.Create(OpCodes.Call, api.Unkeyed);
                il.InsertAfter(call, guard);
                il.InsertAfter(guard, copy);
                sites++;
            }
            body.OptimizeMacros();
        }
        return sites;
    }

    static MethodDefinition Only(TypeDefinition type, string name) =>
        type.Methods.Single(m => m.Name == name && m.HasBody);

    static void Prepend(MethodDefinition method, Func<ILProcessor, Instruction[]> make)
    {
        method.Body.SimplifyMacros();
        var il = method.Body.GetILProcessor();
        var first = method.Body.Instructions[0];
        foreach (var instruction in make(il))
            il.InsertBefore(first, instruction);
        method.Body.OptimizeMacros();
    }

    static void Rewrite(string path, IAssemblyResolver resolver, Action<ModuleDefinition> change)
    {
        Console.WriteLine($"rewriting {path}");
        var tmp = path + ".keyed";
        using (var module = ModuleDefinition.ReadModule(path, new ReaderParameters { AssemblyResolver = resolver, ReadSymbols = false }))
        {
            change(module);
            module.Write(tmp);
        }
        File.Move(tmp, path, overwrite: true);
    }

    static void CopyDir(string from, string to, Func<string, bool>? skip = null)
    {
        Directory.CreateDirectory(to);
        foreach (var file in Directory.GetFiles(from))
            if (skip is null || !skip(Path.GetFileName(file)))
                File.Copy(file, Path.Combine(to, Path.GetFileName(file)));
    }
}
