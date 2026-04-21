"""Comprehensive P2 correctness validation."""

import asyncio
import inspect
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

total_pass = 0
total_fail = 0
errors = []


def check(label, condition, detail=""):
    global total_pass, total_fail
    if condition:
        total_pass += 1
    else:
        total_fail += 1
        msg = f"FAIL: {label}" + (f" -- {detail}" if detail else "")
        errors.append(msg)
        print(f"  {msg}")


def run():
    global total_pass, total_fail, errors

    # =================================================================
    print("=" * 60)
    print("TASK 14: Cost Tracking")
    print("=" * 60)

    from src.models.state import TokenUsage, AppState

    tu0 = TokenUsage()
    check("14.1 zero state", tu0.total == 0)

    tu = TokenUsage()
    tu.add({"input_tokens": 100, "output_tokens": 200})
    tu.add({
        "input_tokens": 50, "output_tokens": 30,
        "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 5,
    })
    check("14.2 cumulative", tu.input_tokens == 150 and tu.output_tokens == 230)
    check("14.2 cache", tu.cache_read_tokens == 10 and tu.cache_write_tokens == 5)
    check("14.2 total", tu.total == 380)

    s = AppState()
    s.total_tokens = 999
    check("14.3 compat setter", s.token_usage.input_tokens == 999)
    s.token_usage.output_tokens = 100
    check("14.3 compat getter", s.total_tokens == 1099)

    s2 = AppState()
    s2.token_usage = TokenUsage(input_tokens=100, output_tokens=200,
                                 cache_read_tokens=30, cache_write_tokens=40)
    d = s2.to_dict()
    check("14.4 to_dict", d["token_usage"]["cache_write_tokens"] == 40)
    check("14.4 compat total", d["total_tokens"] == 300)
    s3 = AppState.from_dict(d)
    check("14.4 roundtrip", s3.token_usage.input_tokens == 100 and s3.token_usage.cache_write_tokens == 40)

    s4 = AppState.from_dict({"total_tokens": 5000})
    check("14.5 old format", s4.token_usage.input_tokens == 5000)

    from src.services.pricing import match_model, calculate_cost, format_cost_report, MODEL_PRICING
    check("14.6 exact", match_model("gpt-4o") == MODEL_PRICING["gpt-4o"])
    check("14.6 prefix", match_model("claude-opus-4-20250514") == MODEL_PRICING["claude-opus-4"])
    check("14.6 strip provider", match_model("openai/gpt-4o-mini") == MODEL_PRICING["gpt-4o-mini"])
    check("14.6 unknown", match_model("nonexistent") is None)

    u = TokenUsage(input_tokens=1_000_000, output_tokens=500_000)
    cost = calculate_cost("claude-opus-4", u)
    expected = 15.0 + 37.5
    check("14.7 opus cost", abs(cost - expected) < 0.001, f"got {cost}")

    u2 = TokenUsage(cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    cost2 = calculate_cost("claude-sonnet-4", u2)
    expected2 = 0.3 + 3.75
    check("14.9 cache pricing", abs(cost2 - expected2) < 0.001, f"got {cost2}")

    report = format_cost_report("claude-sonnet-4", TokenUsage(input_tokens=10000, output_tokens=5000))
    check("14.10 report", "claude-sonnet-4" in report and "$" in report)

    # Fallback pricing
    cost_fb = calculate_cost("unknown-model", TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000))
    check("14.11 fallback", cost_fb > 0)

    print()

    # =================================================================
    print("=" * 60)
    print("TASK 11: Hooks System")
    print("=" * 60)

    from src.services.hooks import HookRegistry, HookEvent, Hook, HookResult

    hr = HookRegistry()
    check("11.1 empty", len(hr.get_hooks()) == 0)

    r = HookResult(hook=Hook(event=HookEvent.PRE_TOOL_USE, command="x"), exit_code=1)
    check("11.3a pre+exit1 blocks", r.blocked is True)
    r2 = HookResult(hook=Hook(event=HookEvent.PRE_TOOL_USE, command="x"), exit_code=0)
    check("11.3b pre+exit0 ok", r2.blocked is False)
    r3 = HookResult(hook=Hook(event=HookEvent.POST_TOOL_USE, command="x"), exit_code=1)
    check("11.3c post never blocks", r3.blocked is False)

    async def test_hooks_fire():
        hr2 = HookRegistry()
        hr2.register(Hook(event=HookEvent.PRE_TOOL_USE, command="exit 1", tool_filter="Bash"))
        results = await hr2.fire(HookEvent.PRE_TOOL_USE, {"tool_name": "Bash"})
        check("11.4a match fires", len(results) == 1)
        check("11.4b exit code nonzero", results[0].exit_code == 1)
        results2 = await hr2.fire(HookEvent.PRE_TOOL_USE, {"tool_name": "FileRead"})
        check("11.4c non-match skip", len(results2) == 0)

        hr3 = HookRegistry()
        hr3.register(Hook(event=HookEvent.POST_TOOL_USE, command="echo test"))
        results3 = await hr3.fire(HookEvent.POST_TOOL_USE, {"tool_name": "Any"})
        check("11.4d wildcard fires", len(results3) == 1)
        check("11.4e stdout", results3[0].stdout == "test")

    asyncio.run(test_hooks_fire())

    hr4 = HookRegistry()
    hr4.load_from_config([
        {"event": "PreToolUse", "command": "echo pre", "tool_filter": "Bash", "timeout": 5},
        {"event": "PostToolUse", "command": "echo post"},
        {"event": "BAD_EVENT", "command": "x"},  # invalid, skipped
    ])
    check("11.5a config load", len(hr4.get_hooks()) == 2)
    check("11.5b timeout", hr4.get_hooks(HookEvent.PRE_TOOL_USE)[0].timeout == 5)

    async def test_env():
        hr5 = HookRegistry()
        hr5.register(Hook(event=HookEvent.PRE_TOOL_USE, command="echo $MC_TOOL_NAME", tool_filter="*"))
        r = await hr5.fire(HookEvent.PRE_TOOL_USE, {"tool_name": "TestTool"})
        check("11.6 MC_TOOL_NAME", r[0].stdout == "TestTool")

    asyncio.run(test_env())

    async def test_timeout():
        hr7 = HookRegistry()
        hr7.register(Hook(event=HookEvent.PRE_TOOL_USE, command="sleep 30", tool_filter="*", timeout=1))
        r = await hr7.fire(HookEvent.PRE_TOOL_USE, {"tool_name": "X"})
        check("11.7a timeout detected", r[0].timed_out is True)
        check("11.7b timeout blocks", r[0].blocked is True)

    asyncio.run(test_timeout())

    print()

    # =================================================================
    print("=" * 60)
    print("TASK 16: Bash Enhancement")
    print("=" * 60)

    from src.tools.bash import is_command_blocked, BashTool

    check("16.1a rm -rf /", is_command_blocked("rm -rf /") is not None)
    check("16.1b rm -rf /*", is_command_blocked("rm -rf /*") is not None)
    check("16.1c mkfs", is_command_blocked("mkfs.ext4 /dev/sda") is not None)
    check("16.1d dd wipe", is_command_blocked("dd if=/dev/zero of=/dev/sda") is not None)
    check("16.1e fork bomb", is_command_blocked(":(){ :|:& };:") is not None)
    check("16.2a safe rm", is_command_blocked("rm -rf ./build") is None)
    check("16.2b safe ls", is_command_blocked("ls -la") is None)
    check("16.2c safe git", is_command_blocked("git push") is None)

    async def test_bash_exec():
        b = BashTool()
        r1 = await b.execute({"command": "rm -rf /"})
        check("16.3 blocked error", r1["is_error"] and "Blocked" in r1["content"])
        r2 = await b.execute({"command": "echo hello_world"})
        check("16.4 normal ok", not r2["is_error"] and "hello_world" in r2["content"])
        r3 = await b.execute({"command": "sleep 10", "timeout": 1})
        check("16.5 timeout", r3["is_error"] and "timed out" in r3["content"].lower())

    asyncio.run(test_bash_exec())

    async def test_streaming():
        b = BashTool()
        chunks = []
        async for c in b.execute_streaming({"command": "rm -rf /"}):
            chunks.append(c)
        check("16.7 streaming blocked", any(
            c["type"] == "error" and "Blocked" in c["content"] for c in chunks
        ))
        chunks2 = []
        async for c in b.execute_streaming({"command": "echo stream_ok"}):
            chunks2.append(c)
        check("16.8a streaming output", any(
            "stream_ok" in c.get("content", "") for c in chunks2 if c["type"] == "text"
        ))
        check("16.8b streaming end", any(
            c["type"] == "end" and c["content"] == "0" for c in chunks2
        ))

    asyncio.run(test_streaming())

    print()

    # =================================================================
    print("=" * 60)
    print("TASK 12: MCP Support")
    print("=" * 60)

    from src.services.mcp import MCPClient
    from src.tools.mcp_tool import MCPToolAdapter
    from src.tools.registry import ToolRegistry

    c = MCPClient(name="test", command="echo", args=["hi"])
    check("12.1 init", c.name == "test" and not c.is_connected)

    spec = {
        "name": "do_thing",
        "description": "Does thing",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
    }
    ad = MCPToolAdapter(mcp_client=c, tool_spec=spec)
    check("12.2a name", ad.name == "mcp__test__do_thing")
    check("12.2b desc", ad.description == "Does thing")
    check("12.2c schema", "x" in ad.input_schema["properties"])
    check("12.2d required", ad.input_schema.get("required") == ["x"])

    schema = ad.get_schema_for_prompt()
    check("12.3 prompt schema", schema["name"] == "mcp__test__do_thing")

    reg = ToolRegistry()
    reg.register(ad)
    c2 = MCPClient(name="s2", command="echo")
    a2 = MCPToolAdapter(mcp_client=c2, tool_spec={"name": "t_a", "description": "A"})
    a3 = MCPToolAdapter(mcp_client=c2, tool_spec={"name": "t_b", "description": "B"})
    reg.register(a2)
    reg.register(a3)
    check("12.4 lookup", reg.get("mcp__test__do_thing") is not None)
    check("12.5 multi", reg.get("mcp__s2__t_a") is not None and reg.get("mcp__s2__t_b") is not None)
    check("12.5 count", len(reg) == 3)

    async def test_mcp_err():
        r = await ad.execute({"x": "test"})
        check("12.6 disconnected error", r["is_error"])

    asyncio.run(test_mcp_err())

    print()

    # =================================================================
    print("=" * 60)
    print("TASK 15: Parallel Orchestration")
    print("=" * 60)

    from src.tools.orchestration import ToolOrchestrator
    from src.models.message import ToolUseBlock as TUB
    from src.tools.file_read import FileReadTool
    from src.tools.file_write import FileWriteTool
    from src.models.tool import ToolResult as TR

    reg3 = ToolRegistry()
    reg3.register(BashTool(), aliases=["bash"])
    reg3.register(FileReadTool(), aliases=["read"])
    reg3.register(FileWriteTool(), aliases=["write"])
    orch = ToolOrchestrator(registry=reg3, state=AppState())

    g1 = orch.analyze_dependencies([TUB(id="1", name="Bash", input={"command": "ls"})])
    check("15.1 single", len(g1) == 1)

    g2 = orch.analyze_dependencies([
        TUB(id="r1", name="FileRead", input={"file_path": "/a.txt"}),
        TUB(id="r2", name="FileRead", input={"file_path": "/b.txt"}),
        TUB(id="r3", name="FileRead", input={"file_path": "/c.txt"}),
    ])
    check("15.2 reads parallel", len(g2) == 1 and len(g2[0].tool_uses) == 3)

    g3 = orch.analyze_dependencies([
        TUB(id="b1", name="Bash", input={"command": "ls"}),
        TUB(id="b2", name="Bash", input={"command": "pwd"}),
        TUB(id="b3", name="Bash", input={"command": "echo"}),
    ])
    check("15.3 bash serial", len(g3) == 3)

    g4 = orch.analyze_dependencies([
        TUB(id="w1", name="FileWrite", input={"file_path": "/x.py", "content": "a"}),
        TUB(id="w2", name="FileWrite", input={"file_path": "/x.py", "content": "b"}),
    ])
    check("15.4 same-file serial", len(g4) == 2)

    g5 = orch.analyze_dependencies([
        TUB(id="r1", name="FileRead", input={"file_path": "/x.py"}),
        TUB(id="w1", name="FileWrite", input={"file_path": "/x.py", "content": "new"}),
    ])
    check("15.5 read-write conflict", len(g5) == 2)

    g6 = orch.analyze_dependencies([
        TUB(id="r1", name="FileRead", input={"file_path": "/a.py"}),
        TUB(id="w1", name="FileWrite", input={"file_path": "/b.py", "content": "x"}),
    ])
    check("15.6 diff files parallel", len(g6) == 1 and len(g6[0].tool_uses) == 2)

    p1 = orch._maybe_track_cd({"command": "cd /tmp && ls"})
    check("15.7a cd tracked", "___MC_CWD___" in p1["command"])
    p2 = orch._maybe_track_cd({"command": "ls -la"})
    check("15.7b no-cd unchanged", "___MC_CWD___" not in p2.get("command", ""))

    result = TR(content="file1\nfile2\n___MC_CWD___\n/tmp", is_error=False)
    orch._update_cwd_from_result({"command": "cd /tmp"}, result)
    check("15.8a cwd updated", orch.state.working_dir == "/tmp")
    check("15.8b marker cleaned", "___MC_CWD___" not in result["content"])

    print()

    # =================================================================
    print("=" * 60)
    print("TASK 13: Agent Communication")
    print("=" * 60)

    from src.tools.agent.send_message import SendMessageTool
    from src.tools.agent.runner import AgentRunner
    from src.models.state import AgentInfo

    async def test_agent():
        state = AppState()
        ag = AgentInfo(agent_id="ag1", name="worker", color="blue", model="m", status="running")
        state.active_agents["ag1"] = ag
        state.agent_mailbox["ag1"] = asyncio.Queue()
        tool = SendMessageTool(state=state)

        r1 = await tool.execute({"to": "worker", "message": "hello"})
        check("13.2a deliver name", not r1["is_error"])
        r2 = await tool.execute({"to": "ag1", "message": "by-id"})
        check("13.2b deliver id", not r2["is_error"])
        check("13.2c queue size", state.agent_mailbox["ag1"].qsize() == 2)

        # Error cases
        t0 = SendMessageTool(state=None)
        r0 = await t0.execute({"to": "x", "message": "hi"})
        check("13.3a no state", r0["is_error"])

        t2 = SendMessageTool(state=AppState())
        r3 = await t2.execute({"to": "ghost", "message": "hi"})
        check("13.3b not found", r3["is_error"])

        state2 = AppState()
        state2.active_agents["ag2"] = AgentInfo(
            agent_id="ag2", name="done", color="r", model="m", status="completed"
        )
        t3 = SendMessageTool(state=state2)
        r4 = await t3.execute({"to": "done", "message": "hi"})
        check("13.3c completed", r4["is_error"])

        state3 = AppState()
        state3.active_agents["ag3"] = AgentInfo(
            agent_id="ag3", name="nobox", color="g", model="m", status="running"
        )
        t4 = SendMessageTool(state=state3)
        r5 = await t4.execute({"to": "nobox", "message": "hi"})
        check("13.3d no mailbox", r5["is_error"])

        # Runner pickup
        state4 = AppState()
        ag4 = AgentInfo(agent_id="ag4", name="rx", color="c", model="m", status="running")
        state4.agent_mailbox["ag4"] = asyncio.Queue()
        AgentRunner._check_mailbox(ag4, state4)
        check("13.4a empty noop", len(ag4.messages) == 0)

        await state4.agent_mailbox["ag4"].put({"from": "main", "message": "task1"})
        AgentRunner._check_mailbox(ag4, state4)
        check("13.4b pickup", len(ag4.messages) == 1)
        check("13.4c content", "task1" in ag4.messages[0].content[0].text)

        ag5 = AgentInfo(agent_id="ag5", name="rx2", color="y", model="m", status="running")
        state4.agent_mailbox["ag5"] = asyncio.Queue()
        await state4.agent_mailbox["ag5"].put({"from": "A", "message": "m1"})
        await state4.agent_mailbox["ag5"].put({"from": "B", "message": "m2"})
        await state4.agent_mailbox["ag5"].put({"from": "C", "message": "m3"})
        AgentRunner._check_mailbox(ag5, state4)
        check("13.4d batch", len(ag5.messages) == 1)
        text = ag5.messages[0].content[0].text
        check("13.4e all msgs", "m1" in text and "m2" in text and "m3" in text)
        check("13.4f sender labels", "[Message from A]" in text and "[Message from C]" in text)

        # No mailbox key -> no crash
        ag6 = AgentInfo(agent_id="ag6", name="no_mb", color="w", model="m", status="running")
        AgentRunner._check_mailbox(ag6, state4)
        check("13.4g no mailbox safe", len(ag6.messages) == 0)

    asyncio.run(test_agent())

    print()

    # =================================================================
    print("=" * 60)
    print("INTEGRATION: Wiring Checks")
    print("=" * 60)

    from src.engine.query import QueryEngine

    sig = inspect.signature(QueryEngine.__init__)
    check("INT.1 hooks param", "hooks" in sig.parameters)

    src_exec = inspect.getsource(QueryEngine._execute_tools_with_permissions)
    check("INT.2a PRE hook", "PRE_TOOL_USE" in src_exec)
    check("INT.2b POST hook", "POST_TOOL_USE" in src_exec)
    check("INT.2c hooks.fire", "self.hooks.fire" in src_exec)

    src_turn = inspect.getsource(QueryEngine.run_turn)
    check("INT.3 token_usage.add", "token_usage.add" in src_turn)

    with open("src/main.py") as f:
        src_main = f.read()
    check("INT.4 HookRegistry", "HookRegistry" in src_main)
    check("INT.5 MCP", "MCPClient" in src_main and "register_mcp_tools" in src_main)
    check("INT.6 SendMessage", "SendMessageTool" in src_main)

    with open("src/commands/builtins.py") as f:
        src_bi = f.read()
    check("INT.7 /cost", "format_cost_report" in src_bi and "token_usage" in src_bi)
    check("INT.8 /clear", "TokenUsage()" in src_bi)
    check("INT.9 /hooks", "cmd_hooks" in src_bi)
    check("INT.10 /mcp", "cmd_mcp" in src_bi)

    print()

    # =================================================================
    print("=" * 60)
    if errors:
        print(f"FAILURES ({total_fail}):")
        for e in errors:
            print(f"  {e}")
        print()
    print(f"TOTAL: {total_pass} passed, {total_fail} failed")
    if total_fail == 0:
        print("ALL P2 CORRECTNESS CHECKS PASSED!")
    else:
        print(f"{total_fail} check(s) need attention")
        sys.exit(1)


if __name__ == "__main__":
    run()
