# Choosing a tool

## Session relevance or client query?

| Question | Use |
| --- | --- |
| Anything the root server already knows | `session_relevance_query` |
| Live state on a machine right now (files, registry, processes) | `client_query` |

Session relevance is a single fast round-trip. A client query costs seconds to
minutes and only reaches agents that are online. When both could work, use
session relevance.

## Which read tool

| You want | Tool |
| --- | --- |
| A computer, by partial name | `find_computers` |
| Everything about one computer | `get_computer` |
| What's applicable/needed on one computer | `applicable_fixlets` |
| A fixlet/task/analysis/baseline, by partial name | `find_content` |
| That content item's full definition | `get_content` |
| Whether an action worked | `get_action_status` |
| What actions exist | `list_actions` |
| Anything else in the REST API | `api_get` (try `api_get('help')`) |

`find_content` returns the `site_path` that `get_content` needs, so those two
chain directly. If `site_path` comes back null, the site name was ambiguous
and `site_path_candidates` lists the options.

## Reading a bounded response

Every tool bounds its output. Two shapes:

- **List tools** (`session_relevance_query`, `find_computers`, `find_content`,
  `client_query_results`) return rows plus `returned`, `offset`,
  `total_available` and `truncated`. Page with `limit`/`offset`.
- **Record tools** (`get_computer`, `get_action_status`, `list_sites`, ...)
  return `data` plus `truncated` and `total_chars`. When a payload is too
  large, `data` is **null** and `note` says what to do instead - it is never
  cut into something that looks complete.

An empty `items` with `truncated: true` means you paged past the end, not that
there is no data.

## Scope: read this before reporting any result

Everything is filtered to what the configured operator can see. Call `whoami`:

- `is_main_operator: true` - you are seeing the whole deployment.
- `is_main_operator: false` - you are seeing a slice, and **cannot tell "does
  not exist" apart from "outside my scope"**. Report counts as "visible to
  this operator", never as the state of BigFix.

A 403 from `list_operators` or `list_roles` usually means exactly this;
`(name of it) of bes users` via session relevance often works where the REST
endpoint does not.

## Writes

Write tools exist only when the server was started with
`BIGFIX_ALLOW_WRITES`; `whoami.writes_enabled` tells you. They all default to
`dry_run=true`, which returns the call that *would* be made without sending
it. Look at that, then repeat with `dry_run=false` if it is right.

There is no tool that deploys an action. `import_bes_content` creates content
but does not run it - deploying stays a human step in the console. Do not
suggest otherwise.
