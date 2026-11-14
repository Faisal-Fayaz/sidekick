"""The importable API: the same core the CLI uses.

Everything here goes through the same turn loop, the same tools, the same
approval gate and the same audit ledger as ``sk run``. The SDK is a way in, not
a second implementation: a feature that lands in one lands in the other.

```python
from sk.sdk import Agent

agent = Agent(session="my-app")
print(agent.turn("what does this repo do?"))
```

**On approval.** ``Agent`` auto-approves by default, because an SDK call has
nobody to ask: the approval gate would deny every write and the agent could do
nothing. That means an SDK call has the same power as ``sk run --yes``. Pass
your own ``approve`` callback to put a gate in front of it:

```python
def approve(name, args):
    return name in ("read_file", "search_code", "repo_map")

agent = Agent(approve=approve)
```
"""

from __future__ import annotations

from typing import Any, Callable

from .config import Config

Approver = Callable[[str, dict], bool]


class Agent:
    """A conversation with the agent, from Python."""

    def __init__(
        self,
        config: Config | None = None,
        session: str = "sdk",
        approve: Approver | None = None,
        **options: Any,
    ):
        """
        config    the configuration to run with; the user's own by default
        session   the audit session name — what `sk audit` will show
        approve   a gate for approval-gated tools; auto-approve when None
        options   any other `sk.agent.run_agent` keyword
        """
        self.config = config or Config.load()
        self.session = session
        self.approve = approve
        self.options = dict(options)
        self.history: list[dict] = []

    def turn(self, message: str, history: list[dict] | None = None) -> str:
        """Run one agent turn and return its answer.

        The turn is the same one the CLI runs: same tools, same approval
        gate, same audit ledger.
        """
        from .agent import run_agent

        answer = run_agent(
            message,
            list(history if history is not None else self.history),
            self.config,
            approve=self.approve,
            session=self.session,
            **self.options,
        )
        self.history.append({"role": "user", "content": message})
        self.history.append({"role": "assistant", "content": answer})
        return answer

    def tool(self, name: str, args: dict | None = None) -> str:
        """Call one tool directly, through the gate and the audit ledger.

        The same path an agent tool call takes, so the audit shows it.
        """
        from .agent import _gated_dispatch, _provider_host

        result, _approved = _gated_dispatch(
            name,
            dict(args or {}),
            approve=self.approve,
            session=self.session,
            provider=self.config.provider,
            host=_provider_host(self.config),
        )
        return result


def turn(
    message: str,
    *,
    config: Config | None = None,
    session: str = "sdk",
    approve: Approver | None = None,
    **options: Any,
) -> str:
    """One agent turn, without keeping a conversation.

    ```python
    from sk.sdk import turn
    print(turn("summarise the README", session="docs-bot"))
    ```
    """
    return Agent(config=config, session=session, approve=approve, **options).turn(message)
