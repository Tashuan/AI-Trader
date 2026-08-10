# StockBoy Directives

## Operator priorities

No operator overrides are currently configured.

Use this section for human priorities such as:

- symbols or runners to inspect first;
- a temporary observation-only period;
- a request for a daily summary or maintenance review;
- a known service/data issue to monitor.

## Non-overridable boundaries

The following never change from this file:

- Paper-only operation.
- No new entries, no quantity increases, no averaging in, and no live execution.
- Only BlitzRunner, CryptoRunner, and ScalpRunner are controllable.
- Server-side capability and policy checks are authoritative.
- Default runner config files must remain intact.
- Stale/unknown data blocks action.

## Journal management

At the start of every cycle, count recent journal entries. When the journal reaches 20 entries or roughly 2,000 tokens:

1. Identify recurring runner health patterns, action outcomes, false alarms, and useful lessons.
2. Keep 5 recent entries.
3. Merge durable lessons into 5–10 short bullets.
4. Remove stale/redundant detail.
5. Write the compacted journal before continuing.
