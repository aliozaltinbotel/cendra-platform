"""Gate-chain composition for the Cendra brain kernel.

Composes the kernel's decision gates (compliance -> certificate -> abstention
-> policy/risk, short-circuit semantics) into a single sync interface that the
touchpoint adapters (T1-T3, see FORK_LEDGER.md) wrap around tool/agent
dispatch.

Stub until Batch 4 (PORTING_MAP.md): the gate chain is composed here from the
Batch 1-2 kernel modules; nothing in this module may import from
core.workflow, core.app or core.agent.
"""
