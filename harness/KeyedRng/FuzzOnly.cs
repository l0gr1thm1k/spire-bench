using System.Threading;
namespace SpireBench.Keyed;
/// <summary> The fuzz-deep builds only (build_keyed.sh --fuzz-deep, for tools/fuzz_keyed.py --deep): the player never drops below 1 HP, and while held there the next hit on an enemy kills it, so a random player reaches acts 2, 3 and the end instead of dying in act 1 or stalling one fight forever. Up to the hit that would have killed the player the game is unchanged. The gateway's build never calls this.
public static class FuzzOnly
{
    /// <summary>Lethal hits survived since an enemy last died: a fight the player cannot hurt (The Insatiable eats every card) ends in death after this many.</summary>
    public const int SavesPerKill = 50;
    static int _playerDown;
    static int _saves;
    static int _released;
    /// <summary>The game is ending the run (a win or an abandon) and kills every player: let it.</summary>
    public static void RunEnding() => Volatile.Write(ref _released, 1);
    public static decimal KeepPlayerAlive(decimal amount, int hp, bool isPlayer)
    {
        if (Volatile.Read(ref _released) == 1) return amount;
        if (isPlayer)
        {
            if (hp <= 0 || amount < hp) return amount;
            if (Interlocked.Increment(ref _saves) <= SavesPerKill) return hp - 1;
            Volatile.Write(ref _released, 1);
            return amount;
        }
        var kill = Volatile.Read(ref _playerDown) == 1 && amount > 0 && amount < hp ? hp : amount;
        if (kill >= hp && hp > 0) Volatile.Write(ref _saves, 0);
        return kill;
    }
    public static int KeepPlayerHp(int value, int hp, bool isPlayer)
    {
        if (!isPlayer || Volatile.Read(ref _released) == 1) return value;
        var kept = hp > 0 && value < 1 ? 1 : value;
        Volatile.Write(ref _playerDown, kept == 1 ? 1 : 0);
        return kept;
    }
}
