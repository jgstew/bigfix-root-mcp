# Client fast query: protocol reference

Reference for the BigFix client query ("fast query" / agent query) REST flow
implemented in [`clientquery.py`](../src/bigfix_root_mcp/clientquery.py).
besapi has no built-in support for this flow, so it is documented here.

Everything below was captured from a live BigFix 11 root server, not inferred
from documentation.

## 1. Submit - `POST /api/clientquery`

Request body:

```xml
<BESAPI>
  <ClientQuery>
    <ApplicabilityRelevance>true</ApplicabilityRelevance>
    <QueryText>computer name</QueryText>
    <Target><ComputerID>6044637</ComputerID></Target>
  </ClientQuery>
</BESAPI>
```

`QueryText` is **client** relevance (evaluated on each agent), not session
relevance. It must be XML-escaped - a query containing `<`, `>` or `&` will
otherwise produce malformed XML. This implementation uses
`xml.sax.saxutils.escape` rather than a CDATA wrapper, which avoids the
`]]>`-inside-relevance edge case entirely.

`Target` accepts (one of):

| Form | XML |
| --- | --- |
| All computers in scope | `<CustomRelevance>TRUE</CustomRelevance>` |
| By ID | `<ComputerID>123</ComputerID>` (repeatable) |
| By name | `<ComputerName>HOST</ComputerName>` (repeatable, must be escaped) |
| By client relevance | `<CustomRelevance>...</CustomRelevance>` |

> **`<AllComputers>` does not work.** It is what besapi's `get_target_xml`
> emits and what this document previously described, but a BigFix 11 root
> server rejects it:
>
> ```
> 400 XML parsing error: no declaration found for element 'AllComputers'
> ```
>
> The other three forms were all accepted in the same test run, so the payload
> around it is fine - the element itself is not in the server's schema.
> Targeting all computers is expressed as client relevance `TRUE`, which is
> applicable on every agent.

Response:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<BESAPI xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="BESAPI.xsd">
	<ClientQuery Resource="http://192.168.5.40:52311/api/clientquery/174">
		<ID>174</ID>
	</ClientQuery>
</BESAPI>
```

The query ID is at `ClientQuery/ID`. Via besapi that is
`result.besobj.ClientQuery.ID`, which is an **lxml objectified element, not an
int** - coerce with `int()` before interpolating it into a URL path.

Note the `Resource` attribute came back as `http://` even though the request
was made over HTTPS; don't feed that attribute back in as a URL.

## 2. Fetch results - `GET /api/clientqueryresults/{id}?output=json`

Real response (single targeted computer, one result row):

```json
{
  "results": [
    {
      "computerID": 6044637,
      "computerName": "HYPERV",
      "subQueryID": 1,
      "isFailure": false,
      "result": "HYPERV",
      "ResponseTime": 1000
    }
  ]
}
```

Field notes:

| Field | Meaning |
| --- | --- |
| `computerID` | BigFix computer ID. One computer can produce **multiple rows**, so count distinct IDs to get "how many agents answered". |
| `subQueryID` | Index of the sub-query when the query text yields a tuple/plural result. |
| `isFailure` | Per-agent evaluation failure flag - an agent that answered with an error still counts as *reported*. |
| `result` | The answer as a string. |
| `ResponseTime` | Agent-reported evaluation time in ms. |

Critical semantics:

- The envelope has **exactly one key**, `results`. There is no `totalResults`,
  no status, and **no completion flag**.
- Results are **cumulative**: each GET returns everything received so far.
  Re-fetching is cheap and idempotent.
- The envelope key is `results` (**plural**) - session relevance
  (`/api/query`) uses `result` (singular). Easy to conflate.
- An empty `results` array early on is normal, not an error.

## 3. Termination heuristics

Because nothing in the protocol says "done", polling needs heuristics. This
server stops on the first of:

1. **`expected_count_reached`** - distinct reporting computers >= the number
   targeted. Only knowable when targeting by ID or name; `AllComputers` and
   `CustomRelevance` targeting yield no expected count (caller may supply one).
2. **`results_stable`** - the distinct-computer count has not changed for
   `stable_polls` consecutive polls, *and* at least one computer has reported.
   The "at least one" guard matters: without it, a query submitted a moment ago
   with zero results would immediately look "stable" and return empty.
3. **`timeout`** - wall-clock bound. **Partial results at timeout are the
   normal outcome**, not an error: offline or unreachable agents simply never
   report, so a wait for 100% is a wait forever.

Tradeoffs worth knowing:

- `results_stable` can fire early on a slow/staggered fleet where agents
  trickle in with gaps longer than `stable_polls * poll_interval`. Raise
  `stable_polls` when targeting many computers.
- Only the *count* is compared between polls, not row content. An agent
  returning additional rows for an already-counted computer does not reset the
  stability counter. Counting distinct computers is deliberate - it is the
  useful notion of progress - but it means "more data still arriving" is not by
  itself a reason to keep waiting.
- The loop never sleeps past its deadline: the final sleep is clamped to the
  remaining time.

For waits longer than a minute or two, prefer the split tools
(`client_query_submit` then repeated `client_query_results`) over the blocking
`client_query`, so no single MCP request stays open for minutes.

## Observed timing

On a small lab fleet, agents that were actively reporting answered a trivial
query (`computer name`) within roughly 5-15 seconds; `ResponseTime` came back
as 1000 ms. A 3-computer query returned 2 answers and stopped on
`results_stable` after ~16 seconds, the third agent never answering - a
textbook example of why partial results must be treated as success.
