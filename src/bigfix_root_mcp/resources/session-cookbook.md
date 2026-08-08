# Session relevance cookbook

Session relevance runs on the **root server** against data it already holds.
No agent is contacted, so it is fast, but it can only answer what has already
been reported. For anything live on an endpoint, use the `client_query` tools.

Every expression below was executed successfully against a BigFix 11 root
server. Pass them to `session_relevance_query`.

## Two rules that catch people out

**There is no row-limiting operator.** `first`, `firsts`, `items` and
`elements` are all undefined - asking for `firsts 100 of bes computers`
returns an error, not 100 rows. Use the tool's `limit`/`offset` parameters.
The server still evaluates the whole query, so `limit` bounds the answer, not
the work.

**Session objects are `bes`-prefixed.** `bes computers`, `bes fixlets`,
`bes tasks`, `bes analyses`, `bes baselines`, `bes actions`, `bes sites`,
`bes users`, `bes computer groups`, `bes properties`. Anything that reads a
machine's disk, registry or processes is *client* relevance and does not exist
here.

## Computers

```
number of bes computers
(name of it, id of it) of bes computers
(name of it, id of it, last report time of it as string) of bes computers
```

Find by name - case-insensitive substring, and exact match:

```
(name of it, id of it) of bes computers whose (name of it as lowercase contains "web")
(id of it) of bes computers whose (name of it = "HYPERV")
```

Agents that have gone quiet, and those that are healthy:

```
(name of it, id of it) of bes computers whose (now - last report time of it > 7 * day)
number of bes computers whose (now - last report time of it < 1 * day)
```

OS spread across the deployment:

```
unique values of (operating system of it) of bes computers
```

Group membership for one computer:

```
(name of it) of bes computer groups of bes computers whose (id of it = 4428228)
```

## Content

Each kind is a separate inspector - `bes fixlets` does not include tasks.

```
number of bes fixlets
(name of it, id of it, name of site of it) of bes fixlets whose (name of it as lowercase contains "relay")
(name of it, id of it) of bes tasks
(name of it, id of it) of bes analyses
(name of it, id of it) of bes baselines
```

Content volumes are large - a reference deployment had 12,395 fixlets and
3,641 tasks - so always filter or rely on the response bounding.

There is **no site-path inspector** (`site path of it`, `path of site of it`
and `type of site of it` are all undefined). `name of site of it` gives the
site's display name; the `find_content` tool joins that to the REST path you
need for `get_content`.

## Actions

```
(name of it, id of it, state of it) of bes actions
unique values of (state of it) of bes actions
```

Observed states: `Open`, `Expired`, `Stopped`.

## Operators, groups, sites, properties

```
(name of it) of bes users
(name of it, id of it) of bes computer groups
names of bes sites
unique values of (name of it) of bes properties
```

`bes users` works for a non-master operator, whereas the `list_operators`
tool's REST endpoint (`/api/operators`) returns 403 unless you are a master
operator - worth knowing when you get a permission error.

## Expressions that look right but are not

| Expression | Result |
| --- | --- |
| `firsts 100 of bes computers` | `The operator "firsts" is not defined.` |
| `first 3 of bes computers` | `The operator "first" is not defined.` |
| `bes computers of bes fixlets whose (...)` | `The operator "bes computers" is not defined.` |
| `relevant bes computers of it` | not defined |
| `site path of it` | not defined |
| `gather url of it` on `bes sites` | not defined |

For "which computers is this fixlet relevant on", use the
`applicable_fixlets` tool (`/api/computer/{id}/fixlets`) from the computer's
side instead.

## Scope

Everything you get back is limited to what the configured operator can see.
Unless `whoami` reports `is_main_operator: true`, counts are a lower bound and
you cannot tell "does not exist" apart from "outside my scope".
