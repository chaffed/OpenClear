# Verifying checks at a branch

When someone cashes a business customer's check at a branch, your teller platform can ask
OpenClear first: was this check issued, for this amount, to this payee, and is it still good?
Positive pay otherwise sees the check only in clearing, after the cash is gone. Once the cash is
paid, the platform records it, so the same check can't be paid again elsewhere. This guide is for
the teller platform's developers and the bank's OpenClear admin.

## How it fits together

```
teller platform ──POST /api/v1/checks/verify──▶ OpenClear   { result: "match", verification_id, expires_at }
   (pay out only on "match"; otherwise follow the bank's procedure)
teller platform ──POST /api/v1/checks/cashed──▶ OpenClear   { paid_item_id, exception_types: [] }
```

1. **Verify.** Send what the teller has from the MICR line (`routing_number`, `account_number`,
   `check_number`), the `amount`, and, if the platform has it, the `payee_name` the teller read,
   with the `teller_id` and `branch`. OpenClear runs the same rules it runs for checks in
   clearing, now, and answers `match` or the first problem:

   | `result` | What it means |
   |---|---|
   | `match` | Issued, for this amount (and payee, when sent), and good: it can be cashed |
   | `not_issued` | The customer never issued this check number on the account |
   | `amount_differs` / `payee_differs` | Issued, but for another amount or payee: `issued` says what |
   | `voided`, `stopped`, `stale` | Voided by the customer, stopped, or older than the bank allows |
   | `stop_differs` | A stop is on file for this check number, but for another amount: the customer decides |
   | `already_paid` | Paid already: in clearing, or cashed at another branch |
   | `customer_review` | The account is on reverse positive pay: the customer reviews each check |
   | `not_on_positive_pay` | OpenClear doesn't know the account |

   `problems` lists every problem found. `issued` (amount, payee, issue date) comes only with a
   decline, to help the teller explain it.
2. **Pay out only on `match`.** For anything else the teller follows the bank's procedure,
   usually: decline, or phone the customer. The customer sees each declined check on their
   Exceptions page for a week, as information.
3. **Record it cashed.** Send the `verification_id` and the amount paid out, within 15 minutes
   of verifying. OpenClear presents the check as paid at once (paid item source `teller`); the
   same check presented anywhere later is a duplicate. A verification can be recorded once. If
   something changed in between (a stop placed a moment ago), `exception_types` says so, and the
   bank should follow up.

## For the bank's OpenClear admin

1. **Settings → API clients:** create a client for the teller platform, in the **Teller
   platform** security group. Its one permission, `check:verify`, lets it verify and record
   cash-outs and nothing else: it can't list issued checks or anything else. No person holds it
   by default.
2. **Decide the fallback.** When OpenClear doesn't answer in time, the teller platform applies
   your policy, for example decline above an amount. OpenClear can't enforce this: configure it in
   the teller platform and train tellers on it (bank CUEC B-CP-2-02).
3. **Watch the action log and your SIEM.** Every verification and cash-out is in the action log
   with the teller and branch the platform sent, and raises a security event (`teller.verify`,
   `teller.verify.declined`, `teller.cashed`). Ten checks that weren't issued within an hour,
   from one teller, branch or client, raise `teller.verify.probing` (high): someone may be trying
   check numbers.

## Notes

- **Speed.** A verification reads the issued check and its stops once each, with no fraud
  scoring or image reading.
- **Plugins.** The bank's matching-rule plugins run in clearing, on a presented check, not on a
  verification.
- **Two branches at once.** Verify takes the same lock on the issued check that clearing takes,
  so a check verified at one branch while it's being cashed at another can't both be paid.
