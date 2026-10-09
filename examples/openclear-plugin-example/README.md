# openclear-plugin-example

This is a working OpenClear plugin you can copy as a template. The guide is [docs/PLUGINS.md](../../docs/PLUGINS.md).

It adds two things:

- **An import format, `paid_items/example_fixed_width`.** These are presented checks in a fixed-width layout, the kind many cores send. The plugin parses each line into the columns OpenClear's own paid-items import expects, then hands the rows to that import. Account lookup, matching, exceptions and per-row results all work as usual.
- **A decision layout, `example_fixed_width`.** It produces a fixed-width file of pay and return decisions for a core to take back.
- **A matching rule, `example_large_round_amount`.** A check for $10,000 or more that's a round thousand becomes a "Large round-amount check" exception.
- **A scoring feature, `example_round_amount`.** It tells the fraud model whether a check's amount is round.
- **A notification channel, `example_log`.** It records and logs the bank's events, using a prefix and a token the bank sets. A real channel would post them to a chat tool.

To try it:

1. Install the package:

   ```sh
   pip install -e examples/openclear-plugin-example
   ```

2. Allow the plugin and restart OpenClear:

   ```sh
   export OPENCLEAR_PLUGINS=example
   ```

3. In OpenClear, go to **Admin → Plugins** and turn on the entries you want for your organization.
4. Upload a file under **Bulk uploads → Other formats**, or call `POST /api/v1/bulk-uploads` with `kind=paid_items` and `format=example_fixed_width`.
5. Fetch the decision file with `GET /api/v1/decisions/file?layout=example_fixed_width`.

A plugin runs inside OpenClear as part of the same program, so it's licensed AGPL-3.0-or-later like OpenClear.
