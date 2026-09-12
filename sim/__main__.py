"""`uv run python -m sim S1` runs one scenario and prints its transcript, tool log, and latency."""

import asyncio
import sys

from sim.personas import PERSONAS
from sim.runner import run_scenario


async def _main(scenario_id: str) -> None:
    if scenario_id not in PERSONAS:
        raise SystemExit(f"unknown scenario {scenario_id!r}; choose from {sorted(PERSONAS)}")
    result = await run_scenario(scenario_id)

    print(f"=== {result.scenario_id} ({result.kind}) ===")
    for speaker, text in result.transcript:
        print(f"{speaker:>8}: {text}")
    print()

    print(f"tool calls ({len(result.tool_calls)}):")
    for call in result.tool_calls:
        print(f"  {call}")
    print()

    if result.latencies_ms:
        sorted_latencies = sorted(result.latencies_ms)
        median = sorted_latencies[len(sorted_latencies) // 2]
        print(f"latency ms: {result.latencies_ms} (median {median}, max {sorted_latencies[-1]})")

    print(f"counters made: {result.counters_made}")
    print(f"offer amounts: {result.offer_amounts}")
    print(f"negotiation status: {result.negotiation_status}")
    if result.escalation_trigger:
        print(f"escalation trigger: {result.escalation_trigger}")
    print(f"ended via: {result.ended_via}")


def main() -> None:
    if len(sys.argv) != 2:
        usage = f"usage: uv run python -m sim <scenario id>, e.g. S1 (one of {sorted(PERSONAS)})"
        raise SystemExit(usage)
    asyncio.run(_main(sys.argv[1]))


if __name__ == "__main__":
    main()
