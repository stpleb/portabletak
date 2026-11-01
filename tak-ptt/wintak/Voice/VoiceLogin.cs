using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

namespace TakPtt.Voice
{
    /// <summary>Thrown with a message for the user when voice cannot be set up from the TAK connection.</summary>
    internal sealed class SetupException : Exception
    {
        public SetupException(string message) : base(message) { }
    }

    /// <summary>
    /// Where and how to sign in to voice, taken from a TAK server connection: the voice server runs
    /// on the TAK server's address (port 64738), the TAK certificate enrolled with that server is
    /// the voice identity, and the voice server's certificate must come from the same certificate
    /// authority trusted for that TAK server. No address, account or password to set up.
    /// </summary>
    internal sealed class VoiceLogin
    {
        public const int DefaultPort = 64738;
        private const string TakDefaultPassword = "atakatak";

        public readonly string ServerName, Host, User;
        public readonly int Port;
        public readonly X509Certificate2 Client;
        private readonly X509Certificate2Collection _authorities;

        private VoiceLogin(string serverName, string host, int port, string user, X509Certificate2 client, X509Certificate2Collection authorities)
        {
            ServerName = serverName;
            Host = host;
            Port = port;
            User = user;
            Client = client;
            _authorities = authorities;
        }

        public static VoiceLogin Create(string serverName, string host, int port, byte[] certificate, string certificatePassword,
            byte[] trust, string trustPassword)
        {
            return Create(serverName, host, port, certificate, new[] { certificatePassword }, trust, new[] { trustPassword });
        }

        /// <summary>Each store is tried with its known passwords in order, then TAK's usual default, then none.</summary>
        public static VoiceLogin Create(string serverName, string host, int port, byte[] certificate, IEnumerable<string> certificatePasswords,
            byte[] trust, IEnumerable<string> trustPasswords)
        {
            if (string.IsNullOrEmpty(host))
                throw new SetupException("Could not read the TAK server's address");
            if (certificate == null || certificate.Length == 0)
                throw new SetupException(serverName + " has no client certificate. TAK PTT signs in with the certificate, so enroll for one in WinTAK first.");
            if (trust == null || trust.Length == 0)
                throw new SetupException("WinTAK has no trust store for " + serverName);
            var client = Open(certificate, certificatePasswords);
            if (!client.HasPrivateKey)
                throw new SetupException("The TAK certificate has no key");
            var authorities = Authorities(trust, trustPasswords);
            if (authorities.Count == 0)
                throw new SetupException("The trust store for " + serverName + " is empty");
            return new VoiceLogin(serverName, host, port, client.GetNameInfo(X509NameType.SimpleName, false), client, authorities);
        }

        private static string[] Passwords(IEnumerable<string> known)
        {
            return known.Concat(new[] { TakDefaultPassword, "" }).Where(p => p != null).Distinct().ToArray();
        }

        private static X509Certificate2 Open(byte[] store, IEnumerable<string> known)
        {
            var passwords = Passwords(known);
            string why = "";
            foreach (var p in passwords)
            {
                try
                {
                    return new X509Certificate2(store, p, X509KeyStorageFlags.UserKeySet | X509KeyStorageFlags.Exportable);
                }
                catch (CryptographicException e)
                {
                    why = e.Message.Trim();
                }
            }
            throw new SetupException("Could not open the TAK certificate with " + passwords.Length + " passwords (" + store.Length + " bytes): " + why);
        }

        private static X509Certificate2Collection Authorities(byte[] store, IEnumerable<string> known)
        {
            var passwords = Passwords(known);
            string why = "";
            foreach (var p in passwords)
            {
                try
                {
                    var all = new X509Certificate2Collection();
                    all.Import(store, p, X509KeyStorageFlags.UserKeySet);
                    return all;
                }
                catch (CryptographicException e)
                {
                    why = e.Message.Trim();
                }
            }
            throw new SetupException("Could not open the TAK trust store with " + passwords.Length + " passwords (" + store.Length + " bytes): " + why);
        }

        /// <summary>
        /// Only a voice server with a certificate from the TAK server's authority is trusted, whatever
        /// the computer's own certificate store says. As on the phones the name in it is not
        /// checked: the authority is the server's own and it is reached by whatever address works.
        /// </summary>
        public bool TrustServer(X509Certificate certificate)
        {
            if (certificate == null) return false;
            try
            {
                using (var chain = new X509Chain())
                {
                    chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
                    chain.ChainPolicy.VerificationFlags = X509VerificationFlags.AllowUnknownCertificateAuthority;
                    chain.ChainPolicy.ExtraStore.AddRange(_authorities);
                    using (var server = new X509Certificate2(certificate))
                    {
                        if (!chain.Build(server)) return false;
                        return chain.ChainElements.Cast<X509ChainElement>().Skip(1).Any(e =>
                            _authorities.Cast<X509Certificate2>().Any(a => a.Thumbprint == e.Certificate.Thumbprint));
                    }
                }
            }
            catch (Exception)
            {
                return false;
            }
        }

        /// <summary>The file for a certificate store, also finding one WinTAK refers to by bare file name; null if none.</summary>
        public static string FindStore(string path, string name)
        {
            if (string.IsNullOrEmpty(path)) return null;
            if (File.Exists(path)) return path;
            string data = Path.Combine(System.Environment.GetFolderPath(System.Environment.SpecialFolder.ApplicationData), "wintak");
            string file = Path.GetFileName(string.IsNullOrEmpty(name) ? path : name);
            if (!Directory.Exists(data) || string.IsNullOrEmpty(file)) return null;
            return Directory.GetFiles(data, file, SearchOption.AllDirectories).FirstOrDefault();
        }
    }
}
