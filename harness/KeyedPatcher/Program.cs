// Copies a built Sts2Headless and its game DLLs into OUT and rewrites them so
// every game draw comes from the episode key (see harness/KeyedRng):
//   MegaRandom.Reinitialise(seed) opens the keyed stream for that seed,
//   MegaRandom.NextULongInner (the one method every draw goes through) draws from it,
//   MegaRandom.NextBytes is refused under a key,
//   Rng.Chaotic, the game's clock-seeded generator, is a keyed one per game,
//   every call to another source of randomness first asks KeyedGuard.Unkeyed,
//   RunSimulator.StartRun takes the key where it took a seed string.
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

    static int Main(string[] argv)
    {
        var opts = Options.Parse(argv);
        if (opts is null)
        {
            Console.Error.WriteLine("usage: KeyedPatcher --bin DIR --lib DIR --keyedrng DLL --localization DIR --out DIR [--plant-unkeyed-draw]");
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
        Rewrite(game, resolver, module =>
        {
            var api = new Api(module, keyed);
            var stream = KeyMegaRandom(module, api);
            KeyChaotic(module, api, stream);
            Console.WriteLine($"  guarded {GuardUnkeyedSources(module, api)} other randomness call sites in sts2.dll");
        });
        Rewrite(Path.Combine(opts.Out, "Sts2Headless.dll"), resolver, module =>
        {
            var api = new Api(module, keyed);
            TakeKeyAtStartRun(module, api);
            if (opts.PlantUnkeyedDraw)
                PlantUnkeyedDraw(module);
            Console.WriteLine($"  guarded {GuardUnkeyedSources(module, api)} other randomness call sites in Sts2Headless.dll");
        });
        Console.WriteLine($"keyed harness written to {opts.Out}");
        return 0;
    }

    sealed record Options(string Bin, string Lib, string KeyedRng, string Localization, string Out, bool PlantUnkeyedDraw)
    {
        public static Options? Parse(string[] argv)
        {
            var named = new Dictionary<string, string>();
            bool plant = false;
            for (int i = 0; i < argv.Length; i++)
            {
                if (argv[i] == "--plant-unkeyed-draw") plant = true;
                else if (argv[i].StartsWith("--") && i + 1 < argv.Length) named[argv[i]] = argv[++i];
                else return null;
            }
            string[] need = { "--bin", "--lib", "--keyedrng", "--localization", "--out" };
            if (need.Any(k => !named.ContainsKey(k)))
                return null;
            return new Options(named["--bin"], named["--lib"], named["--keyedrng"], named["--localization"], named["--out"], plant);
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
            Begin = Get("SpireBench.Keyed.KeyedStreams", "Begin");
            KeyActive = Get("SpireBench.Keyed.KeyedStreams", "get_KeyActive");
            CurrentChaotic = Get("SpireBench.Keyed.KeyedStreams", "CurrentChaotic");
            OpenChaotic = Get("SpireBench.Keyed.KeyedStreams", "OpenChaotic");
            SetChaotic = Get("SpireBench.Keyed.KeyedStreams", "SetChaotic");
            Unkeyed = Get("SpireBench.Keyed.KeyedGuard", "Unkeyed");
        }

        public MethodReference Open, Keyed, Next, RefuseBytes, Begin, Unkeyed, KeyActive, CurrentChaotic, OpenChaotic, SetChaotic;
    }

    static FieldDefinition KeyMegaRandom(ModuleDefinition module, Api api)
    {
        var type = module.GetType(MegaRandom) ?? throw new InvalidOperationException($"{MegaRandom} not found");
        var stream = new FieldDefinition("__keyedStream", FieldAttributes.Assembly, module.TypeSystem.Object);
        type.Fields.Add(stream);

        var reinit = Only(type, "Reinitialise");
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

        var touching = type.Methods.Where(m => m.HasBody && m.Body.Instructions.Any(
            i => i.Operand is FieldReference f && f.Name is "_s0" or "_s1" or "_s2" or "_s3")).Select(m => m.Name).OrderBy(n => n);
        var expected = new[] { "NextBytes", "NextULongInner", "Reinitialise" };
        if (!touching.SequenceEqual(expected))
            throw new InvalidOperationException($"MegaRandom's state is touched by {string.Join(", ", touching)}, not only {string.Join(", ", expected)}; this game build needs a new review");
        Console.WriteLine("  keyed MegaRandom.Reinitialise, NextULongInner, NextBytes");
        return stream;
    }

    /// <summary>Under a key, Rng.Chaotic returns this game's own Rng, made on first use on the chaotic stream.</summary>
    static void KeyChaotic(ModuleDefinition module, Api api, FieldDefinition stream)
    {
        var rng = module.GetType(Rng) ?? throw new InvalidOperationException($"{Rng} not found");
        var getter = Only(rng, "get_Chaotic");
        var ctor = rng.Methods.Single(m => m.IsConstructor && !m.IsStatic && m.Parameters.Count == 2
                                           && m.Parameters[0].ParameterType.MetadataType == MetadataType.UInt32
                                           && m.Parameters[1].ParameterType.MetadataType == MetadataType.Int32);
        var random = rng.Fields.Single(f => f.Name == "_random" && f.FieldType.FullName == MegaRandom);
        var original = getter.Body.Instructions[0];
        Prepend(getter, il =>
        {
            var cast = il.Create(OpCodes.Castclass, rng);
            return new[]
            {
                il.Create(OpCodes.Call, api.KeyActive), il.Create(OpCodes.Brfalse, original),
                il.Create(OpCodes.Call, api.CurrentChaotic), il.Create(OpCodes.Dup), il.Create(OpCodes.Brtrue, cast),
                il.Create(OpCodes.Pop), il.Create(OpCodes.Ldc_I4_0), il.Create(OpCodes.Ldc_I4_0), il.Create(OpCodes.Newobj, ctor),
                il.Create(OpCodes.Dup), il.Create(OpCodes.Ldfld, random), il.Create(OpCodes.Call, api.OpenChaotic),
                il.Create(OpCodes.Stfld, stream),
                il.Create(OpCodes.Call, api.SetChaotic),
                cast, il.Create(OpCodes.Ret),
            };
        });
        Console.WriteLine("  keyed Rng.Chaotic");
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
