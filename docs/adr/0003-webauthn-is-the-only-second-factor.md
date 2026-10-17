# WebAuthn is the only second factor

When a bank or customer requires a second factor for password sign-ins, the only second
factor OpenClear accepts is a WebAuthn credential: a hardware key, a passkey, or an
authenticator built into the device (Touch ID, Windows Hello, Android). Authenticator-app
codes (TOTP) and codes sent by SMS or email were rejected, because all of them can be phished
by a fake sign-in page relaying the code, and the people signing in decide which payments
are made. Nearly every work PC and phone has a built-in authenticator, so requiring WebAuthn
doesn't require buying hardware.

Adding TOTP later would look like an easy convenience. It would weaken every bank that
requires a second factor, so it needs this decision reopened first.
