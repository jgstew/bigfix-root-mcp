"""Workflow prompts.

These encode the tool ordering that actually works, so a client does not have
to rediscover it: find before get, session relevance before client query,
scope check before reporting.

Registered by server.py via register_prompts(mcp). Kept out of server.py only
because that module is already long; there is no import-cycle reason.
"""

SCOPE_REMINDER = (
    "Call whoami first. If is_main_operator is false, every result is a "
    "partial view and you cannot distinguish 'does not exist' from 'outside "
    "my scope' - say so rather than reporting a count as fact."
)


def register_prompts(mcp) -> None:
    """Attach the workflow prompts to a FastMCP server."""

    @mcp.prompt
    def diagnose_computer(name_or_id: str) -> str:
        """Investigate the state of one computer."""
        return (
            f"Investigate the BigFix computer '{name_or_id}'.\n\n"
            "1. Resolve it: if that looks like an ID, call get_computer "
            "directly; otherwise find_computers with it as name_contains, and "
            "if several match, list them and ask which one before going on.\n"
            "2. get_computer for its reported properties. Note last report "
            "time - a stale agent makes everything below stale too.\n"
            "3. applicable_fixlets for what BigFix currently considers "
            "relevant to it.\n"
            "4. Only if you need live state the server cannot have (a file, a "
            "running process, a registry value), use client_query targeted at "
            "that one computer by ID. Do not use target_all.\n\n"
            f"{SCOPE_REMINDER}"
        )

    @mcp.prompt
    def patch_status(target: str) -> str:
        """Summarize outstanding patch content for a computer or group."""
        return (
            f"Report the patch/compliance position for: {target}\n\n"
            "1. Identify the computers involved - find_computers, or session "
            "relevance against bes computer groups if that names a group.\n"
            "2. For each computer (or a representative sample if there are "
            "many), applicable_fixlets gives what is currently relevant.\n"
            "3. Group the findings by content name rather than listing per "
            "machine, and give counts.\n"
            "4. Do not describe anything as 'needing patching' that is merely "
            "relevant - say what BigFix reports and let the reader decide.\n\n"
            f"{SCOPE_REMINDER}"
        )

    @mcp.prompt
    def find_stale_agents(days: str = "7") -> str:
        """Find agents that have stopped reporting."""
        return (
            f"Find BigFix agents that have not reported in {days} days.\n\n"
            "Use session_relevance_query with:\n\n"
            "    (name of it, id of it, last report time of it as string) of "
            f"bes computers whose (now - last report time of it > {days} * day)\n\n"
            "Compare against 'number of bes computers' for the total. Do not "
            "try to limit rows inside the relevance - no such operator exists "
            "- use the limit parameter and report total_available.\n\n"
            "A stale agent is usually powered off, off the network, or has a "
            "stopped client service; the root server cannot tell which. Say "
            "that rather than guessing.\n\n"
            f"{SCOPE_REMINDER}"
        )

    @mcp.prompt
    def troubleshoot_relevance(expression: str, error: str = "") -> str:
        """Fix a relevance expression that BigFix rejected."""
        return (
            "This BigFix relevance expression did not work:\n\n"
            f"    {expression}\n\n"
            f"The server said: {error}\n\n"
            "Read the bigfix://relevance/session-cookbook resource before "
            "guessing. The usual causes, in order of likelihood:\n\n"
            "1. A row-limiting operator. 'first', 'firsts', 'items' and "
            "'elements' do not exist in BigFix relevance. Use the tool's "
            "limit/offset parameters instead.\n"
            "2. Client relevance in a session query. Anything reading a "
            "machine's disk, registry or processes only exists on an agent - "
            "use the client_query tools.\n"
            "3. A missing 'bes' prefix. Session objects are 'bes computers', "
            "'bes fixlets', 'bes actions'...\n"
            "4. A singular expression that matched nothing or matched several. "
            "Use the plural form with a whose(...) filter.\n\n"
            "Propose a corrected expression, then actually run it to confirm "
            "before reporting it as the answer."
        )
