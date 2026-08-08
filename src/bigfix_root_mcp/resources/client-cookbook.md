# Client relevance cookbook (fast query)

Client relevance is evaluated **on each agent**, so it can answer live
questions about a machine's actual current state - files, registry, processes,
services - that the root server does not hold.

Use it through `client_query` (submit and wait) or `client_query_submit` +
`client_query_results` (submit and poll separately, better for long waits).

## Before you reach for it

Client queries are much more expensive than session relevance: every targeted
agent evaluates the expression and reports back over seconds to minutes, and
offline agents never answer at all. If the root server already knows the
answer - computer names, last report times, OS as last reported, applicable
content - use `session_relevance_query` instead.

## Verified expressions

These were executed against live agents:

```
computer name
name of operating system
version of client as string
(computer name, name of operating system, version of client as string)
```

A tuple returns one row per element per computer, distinguished by
`subQueryID`.

## Targeting

Set exactly one of `target_all`, `target_computer_ids`,
`target_computer_names`, `target_relevance`.

Prefer IDs or names when you know them: those are the only forms where the
tool can compute an `expected_count`, which lets polling stop as soon as
everyone has answered instead of waiting out the stability heuristic.

`target_all` is implemented as client relevance `TRUE`. Note that
`<AllComputers>` - what besapi emits and what older notes describe - is
rejected by a BigFix 11 root server with `400 XML parsing error: no
declaration found for element 'AllComputers'`.

## Reading the results

There is **no completion flag**. Results are cumulative, and a partial result
at timeout is the normal outcome, not an error:

- `reported_count` counts **distinct computers**, not rows.
- `isFailure` on a row means that agent hit an evaluation error but did still
  answer - it counts as reported.
- `stop_reason` says why polling ended: `expected_count_reached`,
  `results_stable`, or `timeout`.

Re-fetching with `client_query_results` is cheap and idempotent, so polling a
submitted query yourself is fine.

## Scope and blast radius

Targeting is limited to the configured operator's scope, so `target_all` means
"every computer this operator can see", not every computer in BigFix.

Be deliberate about what you ask for. The BigFix agent runs as SYSTEM/root, so
a client query can read file contents and registry values on every targeted
machine, and the answers come back in the tool response. Ask for the narrowest
thing that answers the question, and do not use it to collect credentials or
other secrets from endpoints.
