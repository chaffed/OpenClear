// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed
using System.Text.Json;

namespace OpenClear;

/// <summary>Every error the SDK throws; the subclasses mark the cases that call for different action.</summary>
public class OpenClearException : Exception
{
    public OpenClearException(string message, int? status = null, string? body = null, Exception? inner = null) : base(message, inner)
    { Status = status; Body = body; }

    public int? Status { get; }
    public string? Body { get; }

    internal static OpenClearException For(int status, string body, TimeSpan? retryAfter)
    {
        string? detail = null, oauth = null;
        try
        {
            using var doc = JsonDocument.Parse(body);
            var root = doc.RootElement;
            if (root.ValueKind == JsonValueKind.Object)
            {
                if (root.TryGetProperty("error", out var e) && e.ValueKind == JsonValueKind.String) oauth = e.GetString();
                if (root.TryGetProperty("detail", out var d))
                    detail = d.ValueKind == JsonValueKind.String ? d.GetString()
                        : d.ValueKind == JsonValueKind.Array ? string.Join("; ", d.EnumerateArray().Select(x => x.TryGetProperty("msg", out var m) ? m.GetString() : x.ToString()))
                        : d.ToString();
                else if (root.TryGetProperty("error_description", out var ed)) detail = ed.GetString();
            }
        }
        catch (JsonException) { detail = body; }
        var message = $"{status}: {detail ?? oauth ?? $"HTTP {status}"}";
        if (oauth is "invalid_client" or "invalid_grant" or "unauthorized_client") return new AuthenticationException(message, status, body);
        return status switch
        {
            401 => new AuthenticationException(message, status, body),
            403 => new PermissionDeniedException(message, status, body),
            404 => new NotFoundException(message, status, body),
            409 => new ConflictException(message, status, body),
            422 => new ValidationException(message, status, body),
            429 => new RateLimitException(message, status, body) { RetryAfter = retryAfter },
            >= 500 => new ServerException(message, status, body),
            _ => new OpenClearException(message, status, body),
        };
    }
}

public sealed class ApiConnectionException(string message, Exception? inner = null) : OpenClearException(message, null, null, inner);
public sealed class AuthenticationException(string message, int? status = null, string? body = null) : OpenClearException(message, status, body);
public sealed class PermissionDeniedException(string message, int? status = null, string? body = null) : OpenClearException(message, status, body);
public sealed class NotFoundException(string message, int? status = null, string? body = null) : OpenClearException(message, status, body);
public sealed class ConflictException(string message, int? status = null, string? body = null) : OpenClearException(message, status, body);
public sealed class ValidationException(string message, int? status = null, string? body = null) : OpenClearException(message, status, body);
public sealed class ServerException(string message, int? status = null, string? body = null) : OpenClearException(message, status, body);
public sealed class RateLimitException(string message, int? status = null, string? body = null) : OpenClearException(message, status, body)
{
    public TimeSpan? RetryAfter { get; init; }
}
public sealed class WebhookVerificationException(string message) : OpenClearException(message);
