# REST endpoint and relevance reference

Everything here was captured from a **live BigFix 11 root server** via
`/api/help`, or by executing the relevance against `/api/query`. Nothing is
inferred from documentation. Recorded because besapi covers only part of this
surface, and because several plausible-looking guesses turn out to be wrong.

## Endpoints used by this server

| Path | Verb | Used by |
| --- | --- | --- |
| `/api/query` | GET | `session_relevance_query`, `find_computers`, `find_content` |
| `/api/clientquery` | POST | `client_query_submit`, `client_query` |
| `/api/clientqueryresults/{id}` | GET | `client_query_results` |
| `/api/computer/{id}` | GET | `get_computer` |
| `/api/computer/{id}/fixlets` | GET | `applicable_fixlets` |
| `/api/computergroups/{site}` | GET | `get_computer_group` |
| `/api/action/{id}` | GET | `get_action` |
| `/api/action/{id}/status` | GET | `get_action_status` |
| `/api/actions` | GET | `list_actions` |
| `/api/{fixlet\|task\|analysis\|baseline}/{site}/{id}` | GET | `get_content` |
| `/api/sites` | GET | `list_sites`, site-path resolution |
| `/api/operators`, `/api/roles` | GET | `list_operators`, `list_roles` |
| `/api/serverinfo` | GET | `get_server_info` |
| `/api/dashboardvariable/{dash}/{var}` | GET / POST | `get_dashboard_variable`, `set_dashboard_variable` |
| `/api/action/{id}/stop` | POST | `stop_action` (gated) |
| `/api/import/{site}` | POST | `import_bes_content` (gated) |

Verified to exist but **deliberately not implemented**: `POST /api/actions`
(deploy), every `DELETE`, `POST /api/operators`, `/api/upload`.

`GET /api/computer/{id}` also exposes `/analyses`, `/baselines`,
`/computergroups`, `/settings`, `/tasks` and `/fixletsandtasks` sub-resources,
reachable today through `api_get`.

## Site paths

`GET /api/sites` is the only authoritative source for a site's REST path. The
element tag gives the type and the `Resource` attribute gives the path:

| Element | Resource path |
| --- | --- |
| `ActionSite` | `master` |
| `ExternalSite` | `external/BES%20Support` |
| `CustomSite` | `custom/autopkg` |
| `OperatorSite` | `operator/API_AutoPkg` |

**A site name can contain a literal `/`.** A real deployment has
`custom/Public%2fWindows` - one site named `Public/Windows`, not a site
`Windows` inside `Public`. Any code that splits a site path on `/` must decode
each segment rather than treat the decoded slash as a separator, and must not
double-encode a path taken from a `Resource` URL. `content.validate_path_segment`
handles this by decoding, validating, then re-encoding, which also makes it
idempotent.

That decode-then-validate order matters for a second reason: `%2e%2e` only
becomes `..` after decoding, so validating the raw segment lets encoded
traversal straight through.

## Relevance findings

### There is no row-limiting operator

`first`, `firsts`, `items` and `elements` are **all undefined** on a BigFix 11
root server:

```
firsts 3 of (1;2;3;4;5)   ->  The operator "firsts" is not defined.
first 3 of (1;2;3;4;5)    ->  The operator "first" is not defined.
items 0 to 2 of (...)     ->  The operator "items" is not defined.
elements 1 to 3 of (...)  ->  The operator "elements" is not defined.
```

Advice to "bound the result set in the relevance itself" is therefore wrong -
it produces an error, not a smaller answer. Response windowing
(`response.bound_list`, the `limit`/`offset` parameters) is the only way to
bound a result. Note this bounds the *response*, not the work: the root server
still evaluates the whole query.

`unique values of` does exist and works.

### There is no site-path inspector

`site path of it`, `path of site of it`, `type of site of it` and
`kind of it` are all undefined. Session relevance can report a content item's
site **name** (`name of site of it`), but the REST API addresses content by
**path**, so the two have to be joined through `/api/sites`. That is what
`content.build_site_path_map` is for.

Also undefined on `bes site`: `custom flag`, `gather url`, `external flag`,
`type`. Defined: `name`, `display name`, `operator site flag`.

### Working expressions

```
number of bes computers
(name of it, id of it) of bes computers
(name of it, id of it, last report time of it as string) of bes computers
(name of it, id of it) of bes computers whose (name of it as lowercase contains "web")
(name of it, id of it, name of site of it) of bes fixlets whose (name of it as lowercase contains "relay")
names of bes sites
```

Content inspectors are separate per kind: `bes fixlets`, `bes tasks`,
`bes analyses`, `bes baselines`. On the reference deployment these returned
12,395 / 3,641 / 118 / 9 items respectively - a good illustration of why
bounding is not optional.

`relevant bes computers of it` is **not** defined; use
`/api/computer/{id}/fixlets` for the applicable-content question instead.

### String literals cannot escape a double quote

There is no portable escape for `"` inside a relevance string literal, so
`find_computers` / `find_content` reject a search term containing one rather
than build an expression that means something other than what was asked.
