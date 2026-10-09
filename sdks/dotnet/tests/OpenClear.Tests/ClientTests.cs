// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed
using System.Net;
using System.Text;
using System.Text.Json;
using OpenClear;
using Xunit;

public class ClientTests
{
    private sealed class Fake : HttpMessageHandler
    {
        public readonly List<HttpRequestMessage> Seen = new();
        public readonly List<string> Bodies = new();
        private readonly Queue<Func<HttpResponseMessage>> _staged;
        public Fake(params Func<HttpResponseMessage>[] staged) => _staged = new(staged);

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Seen.Add(request);
            Bodies.Add(request.Content is null ? "" : await request.Content.ReadAsStringAsync(ct));
            if (request.RequestUri!.AbsolutePath == "/api/v1/oauth/token")
                return Json(200, $"{{\"access_token\":\"t{Seen.Count}\",\"expires_in\":1800}}");
            return _staged.Dequeue()();
        }
    }

    private static HttpResponseMessage Json(int status, string body, Action<HttpResponseMessage>? tweak = null)
    {
        var response = new HttpResponseMessage((HttpStatusCode)status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };
        tweak?.Invoke(response);
        return response;
    }

    private static (OpenClearClient, Fake, List<TimeSpan>) Client(params Func<HttpResponseMessage>[] staged)
    {
        var fake = new Fake(staged);
        var waits = new List<TimeSpan>();
        var client = new OpenClearClient(new OpenClearOptions { BaseUrl = "https://oc.example", ClientId = "occ_x", ClientSecret = "s" }, new HttpClient(fake))
        {
            Delay = (wait, _) => { waits.Add(wait); return Task.CompletedTask; },
        };
        return (client, fake, waits);
    }

    [Fact]
    public async Task Writes_are_retried_with_one_idempotency_key_honouring_Retry_After()
    {
        var (oc, fake, waits) = Client(
            () => throw new HttpRequestException("refused"),
            () => Json(503, "{}"),
            () => Json(429, "{}", r => r.Headers.RetryAfter = new System.Net.Http.Headers.RetryConditionHeaderValue(TimeSpan.FromSeconds(7))),
            () => Json(201, "{\"id\":\"1\",\"account_id\":\"a\",\"check_number\":\"1\"}"));
        var created = await oc.StopPayments.CreateStopPaymentAsync(new StopPaymentCreate { AccountId = "a", CheckNumber = "1" });
        Assert.Equal("1", created.Id);
        var writes = fake.Seen.Where(r => r.RequestUri!.AbsolutePath != "/api/v1/oauth/token").ToList();
        Assert.Equal(4, writes.Count);
        Assert.Single(writes.Select(r => r.Headers.GetValues("Idempotency-Key").Single()).Distinct());
        Assert.Equal(TimeSpan.FromSeconds(7), waits[2]);
        Assert.Contains("\"account_id\":\"a\"", fake.Bodies.Last());
    }

    [Fact]
    public async Task Errors_are_typed_and_a_rejected_token_is_renewed_once()
    {
        var (oc, fake, _) = Client(
            () => Json(401, "{\"detail\":\"expired\"}"),
            () => Json(200, "[]"),
            () => Json(404, "{\"detail\":\"Issued item not found\"}"),
            () => Json(422, "{\"detail\":[{\"loc\":[\"body\",\"issue_date\"],\"msg\":\"Input should be a valid date\"}]}"));
        Assert.Empty(await oc.IssuedItems.ListIssuedItemsAsync());
        Assert.Equal(2, fake.Seen.Count(r => r.RequestUri!.AbsolutePath == "/api/v1/oauth/token"));
        await Assert.ThrowsAsync<NotFoundException>(() => oc.IssuedItems.GetIssuedItemAsync("x"));
        var invalid = await Assert.ThrowsAsync<ValidationException>(() => oc.IssuedItems.CreateIssuedItemAsync(new IssuedItemCreate()));
        Assert.Contains("valid date", invalid.Message);
    }

    [Fact]
    public async Task Next_links_stay_on_the_configured_host_and_act_as_is_sent()
    {
        var (oc, fake, _) = Client(
            () => Json(200, "[{\"id\":\"1\",\"check_number\":\"A\"}]", r => r.Headers.TryAddWithoutValidation("Link", "<http://evil.example/api/v1/issued-items?offset=1&limit=1>; rel=\"next\"")),
            () => Json(200, "[{\"id\":\"2\",\"check_number\":\"B\"}]"),
            () => Json(200, "[]"));
        var numbers = new List<string>();
        await foreach (var item in oc.IssuedItems.IterIssuedItemsAsync(limit: 1)) numbers.Add(item.CheckNumber);
        Assert.Equal(new[] { "A", "B" }, numbers);
        Assert.Equal("oc.example", fake.Seen.Last().RequestUri!.Host);
        await oc.ActingAs("pat@acme.example").IssuedItems.ListIssuedItemsAsync();
        Assert.Equal("pat@acme.example", fake.Seen.Last().Headers.GetValues("OpenClear-Act-As").Single());
    }

    [Fact]
    public void Webhooks_match_what_the_server_signs()
    {
        const string secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw";
        var body = Encoding.UTF8.GetBytes("{\"type\":\"exception.created\",\"data\":{\"exception_id\":\"x\"}}");
        const string serverSignature = "v1,JZLU1/gtgpcxaCdHVqt4//LQX3GkdMekUUNyYTj+seg=";
        Assert.Equal(serverSignature, Webhooks.Sign(secret, "msg_1", 1790000000, body));
        var headers = new Dictionary<string, string> { ["Webhook-Id"] = "msg_1", ["Webhook-Timestamp"] = "1790000000", ["Webhook-Signature"] = serverSignature };
        var at = DateTimeOffset.FromUnixTimeSeconds(1790000000);
        Assert.Equal("exception.created", Webhooks.Verify(secret, headers, body, now: at).GetProperty("type").GetString());
        Assert.Throws<WebhookVerificationException>(() => Webhooks.Verify(secret, headers, body.Concat(new byte[] { 32 }).ToArray(), now: at));
        Assert.Throws<WebhookVerificationException>(() => Webhooks.Verify(secret, headers, body, now: at.AddHours(1)));
    }
}
