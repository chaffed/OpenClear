// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace OpenClear;

/// <summary>Checking that a webhook came from OpenClear (Standard Webhooks). Verify the raw body before parsing it.</summary>
public static class Webhooks
{
    public static readonly TimeSpan DefaultTolerance = TimeSpan.FromMinutes(5);

    /// <summary>The webhook-signature value OpenClear sends (for testing your own receiver).</summary>
    public static string Sign(string secret, string messageId, long timestamp, byte[] body)
    {
        byte[] key;
        try { key = Convert.FromBase64String(secret.StartsWith("whsec_", StringComparison.Ordinal) ? secret[6..] : secret); }
        catch (FormatException) { throw new WebhookVerificationException("The secret isn't a valid whsec_ secret."); }
        var prefix = Encoding.UTF8.GetBytes($"{messageId}.{timestamp}.");
        using var hmac = new HMACSHA256(key);
        return "v1," + Convert.ToBase64String(hmac.ComputeHash(prefix.Concat(body).ToArray()));
    }

    /// <summary>The event, if the signature matches and the timestamp is recent; throws WebhookVerificationException otherwise.</summary>
    public static JsonElement Verify(string secret, IReadOnlyDictionary<string, string> headers, byte[] body, TimeSpan? tolerance = null, DateTimeOffset? now = null)
    {
        string? Header(string name) => headers.FirstOrDefault(h => string.Equals(h.Key, name, StringComparison.OrdinalIgnoreCase)).Value;
        var id = Header("webhook-id");
        var timestamp = Header("webhook-timestamp");
        var signatures = Header("webhook-signature");
        if (id is null || timestamp is null || signatures is null)
            throw new WebhookVerificationException("Missing webhook-id, webhook-timestamp or webhook-signature header.");
        if (!long.TryParse(timestamp, out var sentAt)) throw new WebhookVerificationException("webhook-timestamp isn't a number.");
        var age = (now ?? DateTimeOffset.UtcNow) - DateTimeOffset.FromUnixTimeSeconds(sentAt);
        if (age.Duration() > (tolerance ?? DefaultTolerance))
            throw new WebhookVerificationException("The webhook's timestamp is too old or in the future.");
        var expected = Encoding.ASCII.GetBytes(Sign(secret, id, sentAt, body));
        if (!signatures.Split(' ').Any(candidate => CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(candidate), expected)))
            throw new WebhookVerificationException("The webhook's signature doesn't match.");
        try { return JsonDocument.Parse(body).RootElement.Clone(); }
        catch (JsonException) { throw new WebhookVerificationException("The webhook's body isn't JSON."); }
    }
}
