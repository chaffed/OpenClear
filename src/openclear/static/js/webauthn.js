/* Shared helpers for both WebAuthn ceremonies (login second-factor and security-settings
 * registration). Hand-rolled base64url<->ArrayBuffer conversion rather than relying on
 * the newer PublicKeyCredential.parseCreationOptionsFromJSON()/toJSON() browser APIs, for
 * broader compatibility. Field names below match exactly what the server's `webauthn`
 * Python library produces/expects (see auth/webauthn_service.py) — challenge, user.id,
 * and every credential id are base64url strings in the JSON wire format. */

function base64urlToBuffer(base64url) {
  const padding = "=".repeat((4 - (base64url.length % 4)) % 4);
  const base64 = (base64url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const buffer = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) buffer[i] = raw.charCodeAt(i);
  return buffer.buffer;
}

function bufferToBase64url(buffer) {
  const bytes = new Uint8Array(buffer);
  let str = "";
  for (const b of bytes) str += String.fromCharCode(b);
  return btoa(str).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function getCookie(name) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

function csrfHeaders(extra) {
  return Object.assign({ "X-CSRF-Token": getCookie("csrf_token") || "" }, extra || {});
}

async function loginWebAuthn(nextPath, onError) {
  try {
    const optionsResp = await fetch("/ui/login/webauthn/options", { method: "POST", headers: csrfHeaders() });
    const optionsBody = await optionsResp.json();
    if (!optionsResp.ok) {
      onError(optionsBody.error || "Could not start sign-in.");
      return;
    }

    const publicKey = Object.assign({}, optionsBody, {
      challenge: base64urlToBuffer(optionsBody.challenge),
      allowCredentials: (optionsBody.allowCredentials || []).map((c) =>
        Object.assign({}, c, { id: base64urlToBuffer(c.id) })
      ),
    });

    const assertion = await navigator.credentials.get({ publicKey });

    const credential = {
      id: assertion.id,
      rawId: bufferToBase64url(assertion.rawId),
      type: assertion.type,
      response: {
        clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
        authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
        signature: bufferToBase64url(assertion.response.signature),
        userHandle: assertion.response.userHandle ? bufferToBase64url(assertion.response.userHandle) : null,
      },
    };

    const verifyResp = await fetch(`/ui/login/webauthn/verify?next=${encodeURIComponent(nextPath)}`, {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ credential }),
    });
    const verifyBody = await verifyResp.json();
    if (!verifyResp.ok) {
      onError(verifyBody.error || "Sign-in verification failed.");
      return;
    }
    window.location = verifyBody.redirect || "/ui/";
  } catch (err) {
    onError((err && err.message) || String(err));
  }
}

async function registerWebAuthn(nickname, onSuccess, onError, endpointBase) {
  const base = endpointBase || "/ui/security/webauthn/register";
  try {
    const optionsResp = await fetch(`${base}/options`, {
      method: "POST",
      headers: csrfHeaders(),
    });
    const optionsBody = await optionsResp.json();
    if (!optionsResp.ok) {
      onError(optionsBody.error || "Could not start registration.");
      return;
    }

    const publicKey = Object.assign({}, optionsBody, {
      challenge: base64urlToBuffer(optionsBody.challenge),
      user: Object.assign({}, optionsBody.user, { id: base64urlToBuffer(optionsBody.user.id) }),
      excludeCredentials: (optionsBody.excludeCredentials || []).map((c) =>
        Object.assign({}, c, { id: base64urlToBuffer(c.id) })
      ),
    });

    const created = await navigator.credentials.create({ publicKey });

    const credential = {
      id: created.id,
      rawId: bufferToBase64url(created.rawId),
      type: created.type,
      response: {
        clientDataJSON: bufferToBase64url(created.response.clientDataJSON),
        attestationObject: bufferToBase64url(created.response.attestationObject),
      },
    };

    const verifyResp = await fetch(`${base}/verify`, {
      method: "POST",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ credential, nickname }),
    });
    const verifyBody = await verifyResp.json();
    if (!verifyResp.ok) {
      onError(verifyBody.error || "Registration verification failed.");
      return;
    }
    onSuccess(verifyBody);
  } catch (err) {
    onError((err && err.message) || String(err));
  }
}
