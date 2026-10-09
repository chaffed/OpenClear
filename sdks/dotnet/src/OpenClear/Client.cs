// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed
using System.Net;
using System.Net.Http.Headers;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;

namespace OpenClear;

/// <summary>What the generated endpoint classes call; <see cref="OpenClearClient"/> is the one implementation.</summary>
public interface ITransport
{
    Task<T> CallAsync<T>(string method, string path, IDictionary<string, object?>? query = null, object? json = null,
        IDictionary<string, object?>? form = null, bool platform = false, CancellationToken cancellationToken = default);
    Task<byte[]> CallRawAsync(string method, string path, IDictionary<string, object?>? query = null, object? json = null,
        IDictionary<string, object?>? form = null, bool platform = false, CancellationToken cancellationToken = default);
    IAsyncEnumerable<T> PaginateAsync<T>(string method, string path, IDictionary<string, object?>? query = null,
        bool platform = false, CancellationToken cancellationToken = default);
}

/// <summary>A file to upload: its name, contents and type.</summary>
public sealed record FilePart(string FileName, byte[] Content, string ContentType = "application/octet-stream");

public sealed class OpenClearOptions
{
    /// <summary>e.g. https://openclear.yourbank.example</summary>
    public required string BaseUrl { get; init; }
    /// <summary>An API client's credentials (Admin → API clients). The token is fetched and renewed for you.</summary>
    public string? ClientId { get; init; }
    public string? ClientSecret { get; init; }
    /// <summary>Or a token you already have.</summary>
    public string? AccessToken { get; init; }
    /// <summary>For the platform endpoints.</summary>
    public string? PlatformApiKey { get; init; }
    /// <summary>Retries for connection failures, 429 and 502–504 (default 3).</summary>
    public int MaxRetries { get; init; } = 3;
    public TimeSpan Timeout { get; init; } = TimeSpan.FromSeconds(30);
}

/// <summary>
/// A connection to one OpenClear installation as one API client. Every write gets an
/// Idempotency-Key, kept across retries, so a retried call never acts twice.
/// </summary>
public partial class OpenClearClient : ITransport, IDisposable
{
    private static readonly HashSet<string> Writes = new() { "POST", "PUT", "PATCH", "DELETE" };
    private static readonly HashSet<int> RetryStatuses = new() { 429, 502, 503, 504 };
    internal static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    private readonly OpenClearOptions _options;
    private readonly HttpClient _http;
    private readonly bool _ownsHttp;
    private readonly TokenState _token;
    private readonly Dictionary<string, string> _headers;

    /// <summary>Replaced in tests to skip waiting.</summary>
    public Func<TimeSpan, CancellationToken, Task> Delay { get; set; } = Task.Delay;

    public OpenClearClient(OpenClearOptions options, HttpClient? http = null)
        : this(options, http, new TokenState(options.AccessToken), new Dictionary<string, string>()) { }

    private OpenClearClient(OpenClearOptions options, HttpClient? http, TokenState token, Dictionary<string, string> headers)
    {
        if (options.ClientId is not null && options.ClientSecret is null) throw new ArgumentException("ClientSecret is needed with ClientId.");
        _options = options;
        _ownsHttp = http is null;
        _http = http ?? new HttpClient { Timeout = options.Timeout };
        _token = token;
        _headers = headers;
        BindAreas();
        DecisionFeedHelper = new DecisionFeedHelper(this);
    }

    public string BaseUrl => _options.BaseUrl.TrimEnd('/');

    /// <summary>Consuming the decision feed (see <see cref="DecisionFeedHelper.ConsumeAsync"/>).</summary>
    public DecisionFeedHelper DecisionFeedHelper { get; }

    /// <summary>
    /// A client that acts for one of the bank's people (OpenClear-Act-As): only for an API client
    /// the bank allowed to; it can do what both the client and the person may do.
    /// </summary>
    public OpenClearClient ActingAs(string email, string? customerNumber = null)
    {
        var headers = new Dictionary<string, string>(_headers) { ["OpenClear-Act-As"] = email };
        if (customerNumber is not null) headers["OpenClear-Act-As-Customer"] = customerNumber;
        return new OpenClearClient(_options, _http, _token, headers) { Delay = Delay };
    }

    public void Dispose()
    {
        if (_ownsHttp) _http.Dispose();
        GC.SuppressFinalize(this);
    }

    // --- Authentication -------------------------------------------------------------------

    private sealed class TokenState
    {
        public TokenState(string? fixedToken) { Value = fixedToken; Fixed = fixedToken is not null; ExpiresAt = Fixed ? DateTimeOffset.MaxValue : DateTimeOffset.MinValue; }
        public string? Value;
        public DateTimeOffset ExpiresAt;
        public readonly bool Fixed;
        public readonly SemaphoreSlim Lock = new(1, 1);
    }

    private async Task<string> BearerAsync(bool refresh, CancellationToken ct)
    {
        if (_token.Fixed) return _token.Value ?? throw new AuthenticationException("No access token.");
        if (_options.ClientId is null) throw new AuthenticationException("Pass ClientId and ClientSecret, or an AccessToken.");
        await _token.Lock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (refresh || _token.Value is null || DateTimeOffset.UtcNow >= _token.ExpiresAt.AddSeconds(-60))
            {
                var form = new Dictionary<string, object?> { ["grant_type"] = "client_credentials", ["client_id"] = _options.ClientId, ["client_secret"] = _options.ClientSecret };
                using var response = await SendAsync("POST", "/api/v1/oauth/token", null, null, form, auth: Auth.None, idempotent: false, urlEncoded: true, ct).ConfigureAwait(false);
                using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false));
                _token.Value = body.RootElement.GetProperty("access_token").GetString();
                var seconds = body.RootElement.TryGetProperty("expires_in", out var e) ? e.GetInt32() : 1800;
                _token.ExpiresAt = DateTimeOffset.UtcNow.AddSeconds(seconds);
            }
            return _token.Value!;
        }
        finally
        {
            _token.Lock.Release();
        }
    }

    private enum Auth { Bearer, Platform, None }

    // --- Sending --------------------------------------------------------------------------

    private string Url(string path, IDictionary<string, object?>? query)
    {
        if (path.StartsWith("http://", StringComparison.Ordinal) || path.StartsWith("https://", StringComparison.Ordinal))
        {
            // A Link header's next page: keep only its path and query, so it can't send the next request elsewhere.
            var next = new Uri(path);
            path = next.PathAndQuery;
        }
        var builder = new StringBuilder(BaseUrl).Append(path);
        var first = !path.Contains('?');
        foreach (var (key, value) in query ?? new Dictionary<string, object?>())
        {
            if (value is null) continue;
            var text = value switch { bool b => b ? "true" : "false", IFormattable f => f.ToString(null, System.Globalization.CultureInfo.InvariantCulture), _ => value.ToString() };
            builder.Append(first ? '?' : '&').Append(Uri.EscapeDataString(key)).Append('=').Append(Uri.EscapeDataString(text ?? ""));
            first = false;
        }
        return builder.ToString();
    }

    private static HttpContent? Content(object? json, IDictionary<string, object?>? form, bool urlEncoded)
    {
        if (json is not null) return new StringContent(JsonSerializer.Serialize(json, json.GetType(), Json), Encoding.UTF8, "application/json");
        if (form is null) return null;
        if (urlEncoded)
            return new FormUrlEncodedContent(form.Where(kv => kv.Value is not null).Select(kv => new KeyValuePair<string, string>(kv.Key, kv.Value!.ToString()!)));
        var multipart = new MultipartFormDataContent();
        foreach (var (key, value) in form)
        {
            switch (value)
            {
                case null: break;
                case FilePart file:
                    var part = new ByteArrayContent(file.Content);
                    part.Headers.ContentType = new MediaTypeHeaderValue(file.ContentType);
                    multipart.Add(part, key, file.FileName);
                    break;
                case bool b: multipart.Add(new StringContent(b ? "true" : "false"), key); break;
                default: multipart.Add(new StringContent(value.ToString()!), key); break;
            }
        }
        return multipart;
    }

    private async Task<HttpResponseMessage> SendAsync(string method, string path, IDictionary<string, object?>? query, object? json,
        IDictionary<string, object?>? form, Auth auth, bool idempotent, bool urlEncoded, CancellationToken ct)
    {
        var idempotencyKey = Writes.Contains(method) && idempotent ? Guid.NewGuid().ToString() : null;
        var refreshed = false;
        for (var attempt = 0; ; )
        {
            using var request = new HttpRequestMessage(new HttpMethod(method), Url(path, query)) { Content = Content(json, form, urlEncoded) };
            request.Headers.Accept.ParseAdd("application/json");
            request.Headers.UserAgent.ParseAdd("openclear-sdk-dotnet/0.1");
            foreach (var (key, value) in _headers) request.Headers.TryAddWithoutValidation(key, value);
            if (idempotencyKey is not null) request.Headers.TryAddWithoutValidation("Idempotency-Key", idempotencyKey);
            if (auth == Auth.Bearer) request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", await BearerAsync(false, ct).ConfigureAwait(false));
            if (auth == Auth.Platform) request.Headers.TryAddWithoutValidation("X-Api-Key", _options.PlatformApiKey ?? throw new AuthenticationException("This is a platform endpoint: pass PlatformApiKey."));

            HttpResponseMessage response;
            try
            {
                response = await _http.SendAsync(request, ct).ConfigureAwait(false);
            }
            catch (HttpRequestException ex) when (attempt < _options.MaxRetries)
            {
                _ = ex;
                await Delay(Backoff(attempt, null), ct).ConfigureAwait(false);
                attempt++;
                continue;
            }
            catch (HttpRequestException ex)
            {
                throw new ApiConnectionException($"Couldn't reach OpenClear: {ex.Message}", ex);
            }
            catch (TaskCanceledException ex) when (!ct.IsCancellationRequested)
            {
                if (attempt < _options.MaxRetries) { await Delay(Backoff(attempt, null), ct).ConfigureAwait(false); attempt++; continue; }
                throw new ApiConnectionException("OpenClear didn't answer in time.", ex);
            }

            var status = (int)response.StatusCode;
            if (status == 401 && auth == Auth.Bearer && !_token.Fixed && !refreshed)
            {
                response.Dispose();
                await BearerAsync(true, ct).ConfigureAwait(false);
                refreshed = true;
                continue;
            }
            var inFlight = status == 409 && attempt > 0 && idempotencyKey is not null;
            if ((RetryStatuses.Contains(status) || inFlight) && attempt < _options.MaxRetries)
            {
                var retryAfter = response.Headers.RetryAfter?.Delta;
                response.Dispose();
                await Delay(Backoff(attempt, retryAfter), ct).ConfigureAwait(false);
                attempt++;
                continue;
            }
            if (status >= 400)
            {
                var text = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                var retry = response.Headers.RetryAfter?.Delta;
                response.Dispose();
                throw OpenClearException.For(status, text, retry);
            }
            return response;
        }
    }

    private static TimeSpan Backoff(int attempt, TimeSpan? retryAfter)
    {
        if (retryAfter is { } wait) return wait > TimeSpan.FromSeconds(60) ? TimeSpan.FromSeconds(60) : wait;
        var ms = Math.Min(500 * Math.Pow(2, attempt), 8000) * (0.5 + Random.Shared.NextDouble() / 2);
        return TimeSpan.FromMilliseconds(ms);
    }

    // --- What the generated endpoints call -------------------------------------------------

    public async Task<T> CallAsync<T>(string method, string path, IDictionary<string, object?>? query = null, object? json = null,
        IDictionary<string, object?>? form = null, bool platform = false, CancellationToken cancellationToken = default)
    {
        using var response = await SendAsync(method, path, query, json, form, platform ? Auth.Platform : Auth.Bearer, true, false, cancellationToken).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        return string.IsNullOrEmpty(text) ? default! : JsonSerializer.Deserialize<T>(text, Json)!;
    }

    public async Task<byte[]> CallRawAsync(string method, string path, IDictionary<string, object?>? query = null, object? json = null,
        IDictionary<string, object?>? form = null, bool platform = false, CancellationToken cancellationToken = default)
    {
        using var response = await SendAsync(method, path, query, json, form, platform ? Auth.Platform : Auth.Bearer, true, false, cancellationToken).ConfigureAwait(false);
        return await response.Content.ReadAsByteArrayAsync(cancellationToken).ConfigureAwait(false);
    }

    public async IAsyncEnumerable<T> PaginateAsync<T>(string method, string path, IDictionary<string, object?>? query = null,
        bool platform = false, [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        var next = path;
        var nextQuery = query;
        while (next is not null)
        {
            using var response = await SendAsync(method, next, nextQuery, null, null, platform ? Auth.Platform : Auth.Bearer, true, false, cancellationToken).ConfigureAwait(false);
            var rows = JsonSerializer.Deserialize<List<T>>(await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false), Json) ?? new();
            foreach (var row in rows) yield return row;
            next = NextLink(response.Headers.TryGetValues("Link", out var links) ? string.Join(",", links) : null);
            nextQuery = null;
        }
    }

    public static string? NextLink(string? header)
    {
        if (string.IsNullOrEmpty(header)) return null;
        foreach (var part in header.Split(','))
        {
            var match = System.Text.RegularExpressions.Regex.Match(part, "<([^>]+)>\\s*;\\s*rel=\"?next\"?");
            if (match.Success) return match.Groups[1].Value;
        }
        return null;
    }
}

/// <summary>The usual core job over the decision feed.</summary>
public sealed class DecisionFeedHelper
{
    private readonly OpenClearClient _client;
    internal DecisionFeedHelper(OpenClearClient client) => _client = client;

    /// <summary>The last sequence <paramref name="consumer"/> acknowledged (0 if it never has).</summary>
    public async Task<long> PositionAsync(string consumer, CancellationToken ct = default)
    {
        var rows = await _client.CallAsync<List<JsonElement>>("GET", "/api/v1/decisions/consumers", cancellationToken: ct).ConfigureAwait(false);
        foreach (var row in rows)
            if (row.GetProperty("consumer").GetString() == consumer) return row.GetProperty("through_sequence").GetInt64();
        return 0;
    }

    /// <summary>
    /// Hands every decision after the consumer's acknowledged position to <paramref name="handle"/>, in order,
    /// acknowledging after each page; returns the last sequence handled. If handle throws, nothing after the last
    /// acknowledged page is acknowledged, so the next run starts again from there.
    /// </summary>
    public async Task<long> ConsumeAsync(string consumer, Func<JsonElement, Task> handle, long? after = null, int pageSize = 500, CancellationToken ct = default)
    {
        var position = after ?? await PositionAsync(consumer, ct).ConfigureAwait(false);
        while (true)
        {
            var page = await _client.CallAsync<List<JsonElement>>("GET", "/api/v1/decisions",
                query: new Dictionary<string, object?> { ["after"] = position, ["limit"] = pageSize }, cancellationToken: ct).ConfigureAwait(false);
            if (page.Count == 0) return position;
            foreach (var decision in page) await handle(decision).ConfigureAwait(false);
            position = page[^1].GetProperty("sequence").GetInt64();
            await _client.CallAsync<JsonElement>("POST", "/api/v1/decisions/acknowledge",
                json: new Dictionary<string, object> { ["consumer"] = consumer, ["through_sequence"] = position }, cancellationToken: ct).ConfigureAwait(false);
            if (page.Count < pageSize) return position;
        }
    }
}
