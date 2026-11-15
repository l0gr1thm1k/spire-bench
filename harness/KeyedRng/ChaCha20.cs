using System;
using System.Buffers.Binary;
using System.Numerics;

namespace SpireBench.Keyed;

/// <summary>The ChaCha20 block function of RFC 8439, section 2.3.</summary>
public static class ChaCha20
{
    public static void Block(ReadOnlySpan<byte> key, uint counter, ReadOnlySpan<byte> nonce, Span<uint> output)
    {
        if (key.Length != 32 || nonce.Length != 12 || output.Length != 16)
            throw new ArgumentException("ChaCha20 takes a 32-byte key, a 12-byte nonce and a 16-word output");
        Span<uint> state = stackalloc uint[16];
        state[0] = 0x61707865; state[1] = 0x3320646e; state[2] = 0x79622d32; state[3] = 0x6b206574;
        for (int i = 0; i < 8; i++)
            state[4 + i] = BinaryPrimitives.ReadUInt32LittleEndian(key.Slice(4 * i));
        state[12] = counter;
        for (int i = 0; i < 3; i++)
            state[13 + i] = BinaryPrimitives.ReadUInt32LittleEndian(nonce.Slice(4 * i));
        state.CopyTo(output);
        for (int round = 0; round < 10; round++)
        {
            Quarter(output, 0, 4, 8, 12); Quarter(output, 1, 5, 9, 13);
            Quarter(output, 2, 6, 10, 14); Quarter(output, 3, 7, 11, 15);
            Quarter(output, 0, 5, 10, 15); Quarter(output, 1, 6, 11, 12);
            Quarter(output, 2, 7, 8, 13); Quarter(output, 3, 4, 9, 14);
        }
        for (int i = 0; i < 16; i++)
            output[i] += state[i];
    }

    static void Quarter(Span<uint> x, int a, int b, int c, int d)
    {
        x[a] += x[b]; x[d] = BitOperations.RotateLeft(x[d] ^ x[a], 16);
        x[c] += x[d]; x[b] = BitOperations.RotateLeft(x[b] ^ x[c], 12);
        x[a] += x[b]; x[d] = BitOperations.RotateLeft(x[d] ^ x[a], 8);
        x[c] += x[d]; x[b] = BitOperations.RotateLeft(x[b] ^ x[c], 7);
    }

    /// <summary>Throws unless the block function reproduces RFC 8439's test vector (section 2.3.2).</summary>
    public static void SelfTest()
    {
        var key = new byte[32];
        for (int i = 0; i < 32; i++) key[i] = (byte)i;
        byte[] nonce = { 0, 0, 0, 9, 0, 0, 0, 0x4a, 0, 0, 0, 0 };
        uint[] expected =
        {
            0xe4e7f110, 0x15593bd1, 0x1fdd0f50, 0xc47120a3, 0xc7f4d1c7, 0x0368c033, 0x9aaa2204, 0x4e6cd4c3,
            0x466482d2, 0x09aa9f07, 0x05d7c214, 0xa2028bd9, 0xd19c12b5, 0xb94e16de, 0xe883d0cb, 0x4e3c50a2,
        };
        var got = new uint[16];
        Block(key, 1, nonce, got);
        if (!got.AsSpan().SequenceEqual(expected))
            throw new InvalidOperationException("ChaCha20 does not reproduce the RFC 8439 test vector; refusing to deal");
    }
}
