# mini_claude

`mini_claude` is the terminal coding-assistant engine used by
[EchoClaw](https://github.com/Iams4kura/EchoClaw). It provides an interactive
CLI, headless query engine, tool execution, session persistence, context
compaction, and MCP integration.

## Install

From the EchoClaw repository root:

```bash
python -m pip install -e ./mini_claude
```

Configure an OpenAI-compatible model in
`mini_claude/config/settings.yaml`, then run:

```bash
mclaude
```

See the [repository README](https://github.com/Iams4kura/EchoClaw#readme) for
configuration, available tools, and the relationship between `mini_claude` and
`mini_claw`.
