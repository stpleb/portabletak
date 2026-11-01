using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace TakPtt.Voice
{
    /// <summary>
    /// Just enough protobuf for Mumble's control messages, written by hand so the plugin carries
    /// no protobuf library that could clash with WinTAK's.
    /// </summary>
    internal sealed class Pb
    {
        private readonly MemoryStream _out = new MemoryStream();

        public Pb UInt(int field, ulong value)
        {
            Varint((ulong)(field << 3));
            Varint(value);
            return this;
        }

        public Pb Bool(int field, bool value) { return UInt(field, value ? 1UL : 0UL); }

        public Pb Str(int field, string value) { return Bytes(field, Encoding.UTF8.GetBytes(value)); }

        public Pb Bytes(int field, byte[] value)
        {
            Varint((ulong)((field << 3) | 2));
            Varint((ulong)value.Length);
            _out.Write(value, 0, value.Length);
            return this;
        }

        public byte[] Done() { return _out.ToArray(); }

        private void Varint(ulong v)
        {
            while ((v & ~0x7FUL) != 0)
            {
                _out.WriteByte((byte)((v & 0x7F) | 0x80));
                v >>= 7;
            }
            _out.WriteByte((byte)v);
        }

        /// <summary>A parsed message: field number to its values (ulong for numbers, byte[] for text and bytes).</summary>
        internal sealed class Msg
        {
            internal readonly Dictionary<int, List<object>> Fields = new Dictionary<int, List<object>>();

            public bool Has(int field) { return Fields.ContainsKey(field); }

            public long Num(int field, long otherwise)
            {
                List<object> v;
                return Fields.TryGetValue(field, out v) ? (long)(ulong)v[v.Count - 1] : otherwise;
            }

            public string Text(int field, string otherwise)
            {
                List<object> v;
                return Fields.TryGetValue(field, out v) ? Encoding.UTF8.GetString((byte[])v[v.Count - 1]) : otherwise;
            }
        }

        public static Msg Parse(byte[] b)
        {
            var m = new Msg();
            int at = 0;
            while (at < b.Length)
            {
                ulong tag = ReadVarint(b, ref at);
                int field = (int)(tag >> 3), wire = (int)(tag & 7);
                object value;
                switch (wire)
                {
                    case 0:
                        value = ReadVarint(b, ref at);
                        break;
                    case 1:
                        at += 8;
                        continue;
                    case 2:
                        int n = (int)ReadVarint(b, ref at);
                        if (n < 0 || at + n > b.Length) throw new IOException("bad length in message");
                        var v = new byte[n];
                        Array.Copy(b, at, v, 0, n);
                        at += n;
                        value = v;
                        break;
                    case 5:
                        at += 4;
                        continue;
                    default:
                        throw new IOException("bad field type in message");
                }
                List<object> list;
                if (!m.Fields.TryGetValue(field, out list)) m.Fields[field] = list = new List<object>();
                list.Add(value);
            }
            return m;
        }

        private static ulong ReadVarint(byte[] b, ref int at)
        {
            ulong v = 0;
            for (int shift = 0; shift < 64; shift += 7)
            {
                if (at >= b.Length) throw new IOException("message cut short");
                int c = b[at++];
                v |= (ulong)(c & 0x7F) << shift;
                if ((c & 0x80) == 0) return v;
            }
            throw new IOException("bad number in message");
        }
    }

    /// <summary>
    /// Mumble's voice packets (the legacy format, which every Murmur still speaks to a client that
    /// says it is version 1.4). Going out: header, sequence, size, Opus. Coming in: header, sender's
    /// session, sequence, size, Opus. The top three header bits are the codec, the low five the target.
    /// </summary>
    internal sealed class VoicePacket
    {
        private const int Opus = 4, LastFlag = 0x2000;

        public int Session, Target;
        public long Sequence;
        public byte[] Data;
        public bool Last;

        public static byte[] Encode(int target, long sequence, byte[] opus, int len, bool last)
        {
            var o = new MemoryStream(len + 12);
            o.WriteByte((byte)((Opus << 5) | (target & 0x1F)));
            PutVarint(o, sequence);
            PutVarint(o, last ? len | LastFlag : len);
            o.Write(opus, 0, len);
            return o.ToArray();
        }

        /// <summary>The packet, or null when it is not Opus (Mumble's UDP ping, or an old codec).</summary>
        public static VoicePacket Decode(byte[] b)
        {
            if (b.Length < 1 || b[0] >> 5 != Opus) return null;
            int at = 1;
            long session = GetVarint(b, ref at), sequence = GetVarint(b, ref at), size = GetVarint(b, ref at);
            int len = (int)(size & 0x1FFF);
            if (at + len > b.Length) throw new IOException("voice packet cut short");
            var data = new byte[len];
            Array.Copy(b, at, data, 0, len);
            return new VoicePacket { Session = (int)session, Target = b[0] & 0x1F, Sequence = sequence, Data = data, Last = (size & LastFlag) != 0 };
        }

        public static void PutVarint(Stream o, long value)
        {
            long v = value;
            if (v < 0 && ~v < 4)
            {
                o.WriteByte((byte)(0xFC | (int)~v));
                return;
            }
            if (v < 0)
            {
                o.WriteByte(0xF8);
                v = ~v;
            }
            if (v < 0x80) o.WriteByte((byte)v);
            else if (v < 0x4000) Bytes(o, 0x80 | (v >> 8), v);
            else if (v < 0x200000) Bytes(o, 0xC0 | (v >> 16), v >> 8, v);
            else if (v < 0x10000000) Bytes(o, 0xE0 | (v >> 24), v >> 16, v >> 8, v);
            else if (v < 0x100000000L) Bytes(o, 0xF0, v >> 24, v >> 16, v >> 8, v);
            else Bytes(o, 0xF4, v >> 56, v >> 48, v >> 40, v >> 32, v >> 24, v >> 16, v >> 8, v);
        }

        private static void Bytes(Stream o, params long[] values)
        {
            foreach (long v in values) o.WriteByte((byte)v);
        }

        public static long GetVarint(byte[] b, ref int at)
        {
            int c = Next(b, ref at);
            if ((c & 0x80) == 0) return c;
            if ((c & 0xC0) == 0x80) return ((long)(c & 0x3F) << 8) | (uint)Next(b, ref at);
            if ((c & 0xE0) == 0xC0) return ((long)(c & 0x1F) << 16) | BigEndian(b, ref at, 2);
            if ((c & 0xF0) == 0xE0) return ((long)(c & 0x0F) << 24) | BigEndian(b, ref at, 3);
            if ((c & 0xFC) == 0xF0) return BigEndian(b, ref at, 4);
            if ((c & 0xFC) == 0xF4) return BigEndian(b, ref at, 8);
            if ((c & 0xFC) == 0xF8) return ~GetVarint(b, ref at);
            return ~(long)(c & 0x03);
        }

        private static long BigEndian(byte[] b, ref int at, int n)
        {
            long v = 0;
            for (int i = 0; i < n; i++) v = (v << 8) | (uint)Next(b, ref at);
            return v;
        }

        private static int Next(byte[] b, ref int at)
        {
            if (at >= b.Length) throw new IOException("voice packet cut short");
            return b[at++];
        }
    }
}
