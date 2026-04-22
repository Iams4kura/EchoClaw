"""Webhook 适配器 — FastAPI HTTP 接口，用于调试和健康检查。"""

import logging
import pathlib
import time
from collections import defaultdict, deque
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..models import BotResponse, UnifiedMessage

logger = logging.getLogger(__name__)


class MessageRequest(BaseModel):
    """POST /message 请求体。"""

    user_id: str = "default"
    content: str


class MessageResponse(BaseModel):
    """POST /message 响应体。"""

    text: str
    duration_ms: float = 0


class WebhookAdapter:
    """FastAPI HTTP 适配器。

    端点：
    - POST /message — 发送消息到引擎
    - POST /reset/{user_id} — 重置用户会话
    - GET /health — 健康检查
    - GET /status — 运行状态

    v0.2: handler 可以是 Brain CognitiveLoop 或旧版 SessionManager。
    """

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.app = FastAPI(title="Mini Claw API", version="0.3.0")
        self._start_time = time.time()
        # 推送消息队列（per-user，最多保留 50 条）
        self._notifications: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        # Vue SPA dist 目录
        self._dist_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "web" / "dist"
        # workspace 根目录（用于扫描 skills 等）
        ws = getattr(handler, "_workspace", None)
        self._workspace_root = pathlib.Path(ws.root) if ws and hasattr(ws, "root") else self._dist_dir.parent.parent / "workspace"
        self._setup_routes()
        self._mount_static()

    def push_notification(self, user_id: str, text: str, source: str = "routine") -> None:
        """向指定用户推送一条通知消息。"""
        self._notifications[user_id].append({
            "text": text,
            "source": source,
            "timestamp": time.time(),
        })

    async def _process_message(self, user_id: str, content: str) -> str:
        """统一消息处理：优先 Brain CognitiveLoop，降级 SessionManager。"""
        # Brain CognitiveLoop: 有 .process(UnifiedMessage) -> BotResponse
        if hasattr(self._handler, "process"):
            msg = UnifiedMessage(
                platform="webhook",
                user_id=user_id,
                chat_id=user_id,
                content=content,
            )
            response: BotResponse = await self._handler.process(msg)
            return response.text

        # 旧版 SessionManager 兼容
        if hasattr(self._handler, "get_or_create"):
            session = await self._handler.get_or_create(user_id)
            return await session.handle(content)

        return "（无可用的消息处理器）"

    def _mount_static(self) -> None:
        """挂载 Vue SPA 静态资源（如果 dist 目录存在）。"""
        assets_dir = self._dist_dir / "assets"
        if assets_dir.exists():
            self.app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static")
            logger.info("Vue SPA assets mounted from %s", assets_dir)
        else:
            logger.info("No Vue dist found at %s, falling back to embedded HTML", self._dist_dir)

    def _setup_routes(self) -> None:
        @self.app.get("/", response_class=HTMLResponse)
        async def chat_ui() -> str:
            """浏览器页面：优先 Vue SPA，降级内嵌 HTML。"""
            index_file = self._dist_dir / "index.html"
            if index_file.exists():
                return index_file.read_text(encoding="utf-8")
            return _CHAT_HTML

        @self.app.post("/message", response_model=MessageResponse)
        async def handle_message(request: MessageRequest) -> MessageResponse:
            t0 = time.time()
            try:
                result = await self._process_message(request.user_id, request.content)
            except Exception as e:
                logger.exception("Engine error for user %s", request.user_id)
                result = f"Engine error: {e}"
            duration_ms = (time.time() - t0) * 1000
            return MessageResponse(text=result, duration_ms=round(duration_ms, 1))

        @self.app.post("/reset/{user_id}")
        async def reset_session(user_id: str) -> dict[str, Any]:
            # Brain: 通过 hands manager 重置
            if hasattr(self._handler, "_hands"):
                await self._handler._hands.reset_user(user_id)
                return {"status": "reset", "user_id": user_id}
            # 旧版 SessionManager
            if hasattr(self._handler, "reset"):
                had = await self._handler.reset(user_id)
                return {"status": "reset" if had else "no_session", "user_id": user_id}
            return {"status": "no_handler", "user_id": user_id}

        @self.app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @self.app.get("/status")
        async def status() -> dict[str, Any]:
            uptime = round(time.time() - self._start_time, 1)
            first_boot = (
                hasattr(self._handler, "_bootstrap_prompt")
                and bool(self._handler._bootstrap_prompt)
                and not self._handler._bootstrapped
            )
            # Brain: 从 hands manager 获取活跃数
            if hasattr(self._handler, "_hands"):
                return {
                    "active_sessions": self._handler._hands.active_count,
                    "uptime_seconds": uptime,
                    "first_boot": first_boot,
                }
            # 旧版 SessionManager
            if hasattr(self._handler, "active_count"):
                return {
                    "active_sessions": self._handler.active_count,
                    "uptime_seconds": uptime,
                    "first_boot": first_boot,
                }
            return {"active_sessions": 0, "uptime_seconds": uptime, "first_boot": first_boot}

        @self.app.get("/skills")
        async def list_skills() -> dict[str, Any]:
            """返回所有可用 skill（按来源分组），供前端技能页面和斜杠命令使用。"""
            import re as _re
            _desc_re = _re.compile(r"^description:\s*(.+)$", _re.MULTILINE)

            def _scan_dir(skill_dir: pathlib.Path, seen: set) -> list[dict[str, str]]:
                """扫描目录中的 .md skill 文件。"""
                result: list[dict[str, str]] = []
                if not skill_dir.is_dir():
                    return result
                for entry in sorted(skill_dir.iterdir()):
                    try:
                        if entry.suffix == ".md" and entry.is_file():
                            name = entry.stem
                            if name in seen:
                                continue
                            seen.add(name)
                            text = entry.read_text(encoding="utf-8")[:500]
                            m = _desc_re.search(text)
                            result.append({"name": name, "desc": m.group(1).strip() if m else "skill"})
                        elif entry.is_dir() and (entry / "SKILL.md").exists():
                            name = entry.name
                            if name in seen:
                                continue
                            seen.add(name)
                            text = (entry / "SKILL.md").read_text(encoding="utf-8")[:500]
                            m = _desc_re.search(text)
                            result.append({"name": name, "desc": m.group(1).strip() if m else "skill"})
                    except Exception:
                        continue
                return result

            seen: set[str] = set()

            # 1. mclaw 内置技能
            builtin = [
                {"name": "diary", "desc": "写今日日记 / 查看日记"},
                {"name": "memo", "desc": "记住一件事"},
                {"name": "forget", "desc": "忘记一条记忆"},
                {"name": "recall", "desc": "回忆相关记忆"},
                {"name": "todo", "desc": "管理待办事项"},
                {"name": "summary", "desc": "总结最近的对话"},
                {"name": "mood", "desc": "查看/调整当前情绪"},
                {"name": "heartbeat", "desc": "查看定时任务状态"},
            ]
            seen.update(s["name"] for s in builtin)

            # 2. mclaude 引擎技能（从 mini_claude/.claude/skills/ 加载）
            mclaude_skills_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent / "mini_claude" / ".claude" / "skills"
            mclaude = _scan_dir(mclaude_skills_dir, seen)

            # 3. mclaw workspace 自定义技能
            custom: list[dict[str, str]] = []
            for skill_dir in [self._workspace_root / ".claude" / "skills",
                              self._workspace_root / "skills"]:
                custom.extend(_scan_dir(skill_dir, seen))

            return {
                "mclaw_builtin": builtin,
                "mclaude": mclaude,
                "mclaw_custom": custom,
            }

        @self.app.get("/pending_question/{user_id}")
        async def pending_question(user_id: str) -> dict[str, Any]:
            """获取用户当前挂起的 AskUser 问题。"""
            if not hasattr(self._handler, "_hands"):
                return {"pending": False}
            q = self._handler._hands.get_pending_question(user_id)
            if q is None:
                return {"pending": False}
            return {"pending": True, "question": q["question"], "options": q["options"]}

        @self.app.post("/answer/{user_id}")
        async def submit_answer(user_id: str, request: MessageRequest) -> dict[str, Any]:
            """提交用户对 AskUser 问题的回答。"""
            if not hasattr(self._handler, "_hands"):
                return {"ok": False, "error": "no hands manager"}
            ok = self._handler._hands.submit_answer(user_id, request.content)
            return {"ok": ok}

        @self.app.get("/thinking/{user_id}")
        async def thinking_state(user_id: str) -> dict[str, Any]:
            """获取用户当前的思考状态（前端轮询展示思考过程）。"""
            if not hasattr(self._handler, "get_thinking_state"):
                return {"thinking": False}
            state = self._handler.get_thinking_state(user_id)
            if state is None or not state.is_processing:
                return {"thinking": False}
            steps = [
                {
                    "step": s.step_number,
                    "name": s.step_name,
                    "status": s.status,
                    "detail": s.detail,
                }
                for s in state.thinking_steps
            ]
            return {
                "thinking": True,
                "original_message": state.original_message or "",
                "steps": steps,
                "queued_count": state.queue.qsize(),
            }

        @self.app.get("/notifications/{user_id}")
        async def get_notifications(user_id: str) -> dict[str, Any]:
            """取走用户的推送通知（取后即清）。

            同时取走广播通知（user_id="default"），确保启动问候等
            全局消息能送达所有用户。
            """
            items: list[dict[str, Any]] = []
            # 用户专属通知
            q = self._notifications.get(user_id)
            if q:
                items.extend(q)
                q.clear()
            # 广播通知（default）：所有用户可见，首个拉取者消费
            bq = self._notifications.get("default")
            if bq:
                items.extend(bq)
                bq.clear()
            return {"notifications": items}

        # --- New API endpoints for Console UI ---

        @self.app.get("/api/routines")
        async def list_routines() -> list[dict[str, Any]]:
            """解析 HEARTBEAT.md 返回定时任务列表。"""
            workspace = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "workspace"
            hb_file = workspace / "HEARTBEAT.md"
            if not hb_file.exists():
                return []
            routines: list[dict[str, Any]] = []
            current_name = ""
            current_body: list[str] = []
            for line in hb_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("## "):
                    if current_name:
                        routines.append({
                            "id": str(len(routines)),
                            "name": current_name,
                            "content": "\n".join(current_body).strip(),
                        })
                    current_name = stripped[3:].strip()
                    current_body = []
                elif current_name:
                    current_body.append(line)
            if current_name:
                routines.append({
                    "id": str(len(routines)),
                    "name": current_name,
                    "content": "\n".join(current_body).strip(),
                })
            return routines

        @self.app.post("/api/routines/{routine_id}/trigger")
        async def trigger_routine(routine_id: str, request: MessageRequest) -> dict[str, Any]:
            """手动触发心跳任务：根据 routine_id 查找任务内容，发送给引擎执行。"""
            routines = await list_routines()
            routine = next((r for r in routines if r["id"] == routine_id), None)
            if not routine:
                return {"ok": False, "error": "任务不存在"}
            # 将任务内容作为消息发送给引擎
            prompt = f"[定时任务手动触发] {routine['name']}：{routine['content']}"
            try:
                result = await self._process_message(request.user_id, prompt)
                # 推送结果通知给用户（不加前缀，直接发内容）
                self.push_notification(request.user_id, result, source="routine")
                return {"ok": True, "message": "已触发并执行完成"}
            except Exception as e:
                logger.exception("手动触发任务 %s 失败", routine_id)
                return {"ok": False, "error": str(e)}

        @self.app.get("/api/config")
        async def get_config() -> dict[str, Any]:
            """返回当前运行配置（脱敏）。"""
            try:
                from ...config import load_config
                cfg = load_config()

                # 引擎模型：从 mini_claude settings.yaml 读取
                engine_model = "(未配置)"
                try:
                    import yaml
                    mc_settings = pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent / "mini_claude" / "config" / "settings.yaml"
                    if mc_settings.exists():
                        with open(mc_settings, encoding="utf-8") as f:
                            mc_cfg = yaml.safe_load(f) or {}
                        engine_model = mc_cfg.get("llm", {}).get("default_model") or "(未配置)"
                except Exception:
                    pass

                return {
                    "运行模式": {
                        "模式": "个人分身" if cfg.is_personal else "多用户平台",
                        "owner_id": cfg.middleware.owner_id or "(未设置)",
                    },
                    "引擎": {
                        "模型": engine_model,
                        "权限模式": cfg.engine.permission_mode,
                    },
                    "Brain": {
                        "模型": cfg.brain.model or "(复用引擎)",
                        "temperature": cfg.brain.temperature,
                        "max_tokens": cfg.brain.max_tokens,
                    },
                    "服务器": {
                        "host": cfg.server.host,
                        "port": cfg.server.port,
                    },
                    "定时任务": {
                        "启用": "是" if cfg.routine.enabled else "否",
                    },
                }
            except Exception as e:
                return {"error": str(e)}


_CHAT_HTML = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mini Claw</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:#1a1a2e;color:#e0e0e0;height:100vh;display:flex;flex-direction:column}
header{padding:12px 20px;background:#16213e;border-bottom:1px solid #0f3460;
  display:flex;align-items:center;justify-content:space-between}
header .title{font-size:18px;font-weight:600;color:#e94560}
header .user-bar{display:flex;align-items:center;gap:10px;font-size:13px}
header .user-bar .uid{color:#e94560;font-weight:600}
header .user-bar button{padding:4px 10px;border:1px solid #0f3460;border-radius:4px;
  background:transparent;color:#e0e0e0;cursor:pointer;font-size:12px}
header .user-bar button:hover{background:#0f3460}
header .user-bar button.reset{border-color:#e94560;color:#e94560}
header .user-bar button.reset:hover{background:#e94560;color:#fff}
#status-bar{padding:6px 20px;background:#0f1a2e;font-size:12px;color:#888;
  border-bottom:1px solid #0f3460;display:flex;gap:20px}
#chat{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}
.msg{max-width:80%;padding:10px 14px;border-radius:12px;line-height:1.5;
  word-wrap:break-word;white-space:pre-wrap;font-size:14px}
.msg.user{align-self:flex-end;background:#0f3460;color:#e0e0e0;border-bottom-right-radius:4px}
.msg.bot{align-self:flex-start;background:#16213e;color:#e0e0e0;border-bottom-left-radius:4px;
  border:1px solid #0f3460}
.msg.bot code{background:#0a0a1a;padding:2px 5px;border-radius:3px;font-size:13px}
.msg.bot pre{background:#0a0a1a;padding:10px;border-radius:6px;overflow-x:auto;margin:6px 0}
.msg.bot pre code{background:none;padding:0}
.msg .meta{font-size:11px;color:#888;margin-top:4px}
.msg .meta .dur{margin-left:8px;color:#e94560}
.typing{align-self:flex-start;color:#888;font-style:italic;font-size:13px}
.system-msg{align-self:center;color:#888;font-size:12px;font-style:italic}
.time-sep{text-align:center;font-size:11px;color:#666;margin:8px 0;user-select:none}
.thinking-panel{align-self:flex-start;max-width:80%;background:#111a2e;
  border:1px solid #1a3a5c;border-radius:10px;padding:10px 14px;margin-bottom:4px;
  font-size:12px;color:#8899aa;transition:all .3s ease}
.thinking-panel.collapsed{max-height:0;padding:0 14px;overflow:hidden;opacity:0;
  border:none;margin:0}
.thinking-panel .tp-header{display:flex;align-items:center;gap:6px;margin-bottom:6px;
  color:#e94560;font-weight:600;font-size:13px;cursor:pointer}
.thinking-panel .tp-header .spinner{display:inline-block;width:12px;height:12px;
  border:2px solid #e94560;border-top-color:transparent;border-radius:50%;
  animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.thinking-panel .tp-step{padding:2px 0;display:flex;align-items:center;gap:6px}
.thinking-panel .tp-step .dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.thinking-panel .tp-step .dot.running{background:#e94560;box-shadow:0 0 4px #e94560}
.thinking-panel .tp-step .dot.done{background:#4caf50}
.thinking-panel .tp-step .dot.cancelled{background:#888}
.queued-hint{align-self:flex-end;color:#e94560;font-size:12px;font-style:italic;
  padding:4px 10px;background:#1a1a2e;border:1px dashed #e94560;border-radius:8px}
#input-area{padding:12px 20px;background:#16213e;border-top:1px solid #0f3460;
  display:flex;gap:10px}
#input{width:100%;padding:10px 14px;border:1px solid #0f3460;border-radius:8px;
  font-size:14px;outline:none;background:#1a1a2e;color:#e0e0e0;resize:none;
  min-height:42px;max-height:120px;font-family:inherit}
#input:focus{border-color:#e94560}
#send{padding:10px 24px;background:#e94560;color:#fff;border:none;border-radius:8px;
  font-size:14px;cursor:pointer;font-weight:600;white-space:nowrap}
#send:hover{background:#c73650}
#send:disabled{background:#555;cursor:not-allowed}
#input-wrap{flex:1;position:relative}
#cmd-panel{display:none;position:absolute;bottom:100%;left:0;right:0;
  margin-bottom:6px;background:#16213e;border:1px solid #0f3460;border-radius:8px;
  overflow:hidden;box-shadow:0 -4px 16px rgba(0,0,0,.4)}
#cmd-panel .cmd-item{padding:8px 14px;display:flex;align-items:center;gap:10px;
  cursor:pointer;font-size:13px}
#cmd-panel .cmd-item:hover,#cmd-panel .cmd-item.active{background:#0f3460}
#cmd-panel .cmd-name{color:#e94560;font-weight:600;min-width:70px}
#cmd-panel .cmd-desc{color:#aaa}
.ask-card{align-self:flex-start;max-width:80%;background:#1c2a4a;border:1px solid #e94560;
  border-radius:12px;padding:14px 18px;display:flex;flex-direction:column;gap:10px}
.ask-card .ask-q{font-size:14px;line-height:1.5;color:#f0f0f0}
.ask-card .ask-opts{display:flex;flex-wrap:wrap;gap:8px}
.ask-card .ask-opt{padding:6px 14px;background:#0f3460;border:1px solid #e94560;
  border-radius:6px;color:#e0e0e0;cursor:pointer;font-size:13px}
.ask-card .ask-opt:hover{background:#e94560;color:#fff}
.ask-card .ask-input-row{display:flex;gap:8px}
.ask-card .ask-input{flex:1;padding:8px 12px;border:1px solid #0f3460;border-radius:6px;
  background:#1a1a2e;color:#e0e0e0;font-size:13px;outline:none}
.ask-card .ask-input:focus{border-color:#e94560}
.ask-card .ask-submit{padding:8px 16px;background:#e94560;color:#fff;border:none;
  border-radius:6px;cursor:pointer;font-size:13px}
</style>
</head>
<body>
<header>
  <span class="title">Mini Claw</span>
  <div class="user-bar">
    <span>User: <span class="uid" id="uid"></span></span>
    <button onclick="switchUser()">切换用户</button>
    <button class="reset" onclick="resetSession()">重置会话</button>
  </div>
</header>
<div id="status-bar">
  <span id="st-sessions">会话: -</span>
  <span id="st-uptime">运行: -</span>
</div>
<div id="chat"></div>
<div id="input-area">
  <div id="input-wrap">
    <div id="cmd-panel"></div>
    <textarea id="input" rows="1" placeholder="输入消息，/ 查看命令..." autofocus></textarea>
  </div>
  <button id="send">发送</button>
</div>
<script>
const chat=document.getElementById("chat"),input=document.getElementById("input"),
  sendBtn=document.getElementById("send"),uidEl=document.getElementById("uid");

// 从 URL ?user=xxx 读取用户 ID，默认随机生成并记住
let userId=new URLSearchParams(location.search).get("user")
  ||localStorage.getItem("claw_last_user")
  ||"user_"+Math.random().toString(36).slice(2,6);
localStorage.setItem("claw_last_user",userId);
uidEl.textContent=userId;
document.title="Mini Claw - "+userId;

// --- 聊天记录持久化 ---
const storageKey=()=>"claw_history_"+userId;
function saveHistory(){
  const msgs=[];
  chat.querySelectorAll(".msg,.system-msg").forEach(el=>{
    if(el.classList.contains("system-msg")){
      msgs.push({type:"system",text:el.textContent});
    }else{
      const cls=el.classList.contains("user")?"user":"bot";
      const meta=el.querySelector(".meta");
      const clone=el.cloneNode(true);
      const m2=clone.querySelector(".meta");if(m2)m2.remove();
      const content=cls==="user"?clone.textContent:clone.innerHTML;
      const ts=parseInt(el.dataset.ts)||0;
      const dur=meta&&meta.querySelector(".dur")?meta.querySelector(".dur").textContent:"";
      // 兼容：同时保存 ts 和 time，旧格式回退用
      const time=meta?meta.childNodes[0].textContent:"";
      msgs.push({type:cls,content,ts,time,dur});
    }
  });
  try{localStorage.setItem(storageKey(),JSON.stringify(msgs))}catch(e){}
}
function loadHistory(){
  let msgs;
  try{msgs=JSON.parse(localStorage.getItem(storageKey()))}catch(e){return}
  if(!msgs||!msgs.length)return;
  let prevTs=0;
  msgs.forEach(m=>{
    if(m.type==="system"){
      const d=document.createElement("div");d.className="system-msg";
      d.textContent=m.text;chat.appendChild(d);
    }else{
      const ts=m.ts||0;
      // 动态插入时间分隔符（每次加载重新计算，日期格式自动更新）
      if(ts&&prevTs&&ts-prevTs>=3600000){
        const sep=document.createElement("div");sep.className="time-sep";
        sep.dataset.ts=ts;sep.textContent=formatTimeSeparator(ts);
        chat.appendChild(sep);
      }
      const d=document.createElement("div");d.className="msg "+m.type;
      d.dataset.ts=ts;
      if(m.type==="bot"){d.innerHTML=m.content}else{d.textContent=m.content}
      const meta=document.createElement("div");meta.className="meta";
      meta.textContent=ts?formatMsgTime(ts):(m.time||"");
      if(m.dur){const s=document.createElement("span");s.className="dur";
        s.textContent=m.dur;meta.appendChild(s)}
      d.appendChild(meta);chat.appendChild(d);
      if(ts)prevTs=ts;
    }
  });
  chat.scrollTop=chat.scrollHeight;
}

function switchUser(){
  const name=prompt("输入用户 ID（不同 ID = 独立会话）:",
    "user_"+Math.random().toString(36).slice(2,6));
  if(name&&name.trim()){location.href="/?user="+encodeURIComponent(name.trim())}
}
async function resetSession(){
  if(!confirm("重置 "+userId+" 的会话？对话记忆将被提取保存。"))return;
  await fetch("/reset/"+encodeURIComponent(userId),{method:"POST"});
  addSystem("会话已重置");
  try{localStorage.removeItem(storageKey())}catch(e){}
  refreshStatus();
}
function formatMsgTime(ts){
  const d=new Date(ts);
  return d.getHours().toString().padStart(2,"0")+":"+d.getMinutes().toString().padStart(2,"0");
}
function formatTimeSeparator(ts){
  const d=new Date(ts),now=new Date();
  const today=new Date(now.getFullYear(),now.getMonth(),now.getDate());
  const yesterday=new Date(today-86400000);
  const msgDay=new Date(d.getFullYear(),d.getMonth(),d.getDate());
  const hm=formatMsgTime(ts);
  if(msgDay.getTime()===today.getTime())return hm;
  if(msgDay.getTime()===yesterday.getTime())return "昨天 "+hm;
  return (d.getMonth()+1)+"月"+d.getDate()+"日 "+hm;
}
function maybeInsertTimeSep(newTs){
  const allMsgs=chat.querySelectorAll(".msg");
  if(!allMsgs.length)return;
  const lastTs=parseInt(allMsgs[allMsgs.length-1].dataset.ts)||0;
  if(!lastTs||newTs-lastTs<3600000)return;
  const sep=document.createElement("div");sep.className="time-sep";
  sep.dataset.ts=newTs;sep.textContent=formatTimeSeparator(newTs);
  chat.appendChild(sep);
}
function addMsg(text,cls,durationMs){
  const now=Date.now();
  maybeInsertTimeSep(now);
  const d=document.createElement("div");d.className="msg "+cls;
  d.dataset.ts=now;
  if(cls==="bot"){d.innerHTML=escapeHtml(text)}else{d.textContent=text}
  const m=document.createElement("div");m.className="meta";
  m.textContent=formatMsgTime(now);
  if(durationMs){const s=document.createElement("span");s.className="dur";
    s.textContent=(durationMs/1000).toFixed(1)+"s";m.appendChild(s)}
  d.appendChild(m);chat.appendChild(d);chat.scrollTop=chat.scrollHeight;
  saveHistory();
}
function addSystem(text){
  const d=document.createElement("div");d.className="system-msg";
  d.textContent="— "+text+" —";chat.appendChild(d);chat.scrollTop=chat.scrollHeight;
  saveHistory();
}
function escapeHtml(s){
  s=s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  s=s.replace(/```([\\s\\S]*?)```/g,"<pre><code>$1</code></pre>");
  s=s.replace(/`([^`]+)`/g,"<code>$1</code>");
  return s;
}
let isProcessing=false,thinkingPanel=null,thinkingTimer=null;

function createThinkingPanel(){
  const p=document.createElement("div");p.className="thinking-panel";
  p.innerHTML='<div class="tp-header"><span class="spinner"></span>思考中...</div><div class="tp-steps"></div>';
  return p;
}
function updateThinkingPanel(panel,steps){
  const container=panel.querySelector(".tp-steps");
  container.innerHTML=steps.map(s=>
    '<div class="tp-step"><span class="dot '+s.status+'"></span>'+
    '<span>'+s.detail+'</span></div>').join("");
  chat.scrollTop=chat.scrollHeight;
}
function collapseThinkingPanel(panel){
  if(!panel)return;
  const header=panel.querySelector(".tp-header");
  if(header){header.innerHTML='<span style="color:#4caf50">✓</span> 思考完成（点击展开）';
    header.onclick=()=>{panel.classList.toggle("collapsed")}}
  setTimeout(()=>panel.classList.add("collapsed"),800);
}
function startThinkingPoll(){
  if(thinkingTimer)return;
  thinkingTimer=setInterval(async()=>{
    try{
      const r=await fetch("/thinking/"+userId);const j=await r.json();
      if(j.thinking&&thinkingPanel){updateThinkingPanel(thinkingPanel,j.steps||[])}
    }catch(e){}
  },1500);
}
function stopThinkingPoll(){
  if(thinkingTimer){clearInterval(thinkingTimer);thinkingTimer=null}
}

async function send(){
  const text=input.value.trim();if(!text)return;
  input.value="";input.style.height="42px";
  const isBtw=text.startsWith("/btw ");

  addMsg(text,"user");

  if(isProcessing&&!isBtw){
    // 普通消息排队：显示排队提示，请求会阻塞直到服务端处理
    const hint=document.createElement("div");
    hint.className="queued-hint";hint.textContent="已排队，等待当前任务完成...";
    chat.appendChild(hint);chat.scrollTop=chat.scrollHeight;
    try{
      const r=await fetch("/message",{method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({user_id:userId,content:text})});
      const j=await r.json();
      hint.remove();
      addMsg(j.text,"bot",j.duration_ms);
    }catch(e){hint.remove();addMsg("请求失败: "+e.message,"bot")}
    refreshStatus();
    return;
  }

  if(isBtw&&isProcessing){
    // /btw 打断：发送请求，当前 pending 请求会收到 [interrupted]
    // 移除当前思考面板（会被新的替换）
    if(thinkingPanel){collapseThinkingPanel(thinkingPanel);thinkingPanel=null}
    stopThinkingPoll();
  }

  isProcessing=true;
  input.placeholder="输入 /btw 补充信息...";
  thinkingPanel=createThinkingPanel();
  chat.appendChild(thinkingPanel);chat.scrollTop=chat.scrollHeight;
  startThinkingPoll();

  try{
    const r=await fetch("/message",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({user_id:userId,content:text})});
    const j=await r.json();
    stopThinkingPoll();
    collapseThinkingPanel(thinkingPanel);thinkingPanel=null;
    // 过滤 [interrupted] 响应（被 /btw 取消的）
    if(j.text!=="[interrupted]"){addMsg(j.text,"bot",j.duration_ms)}
  }catch(e){
    stopThinkingPoll();
    if(thinkingPanel){thinkingPanel.remove();thinkingPanel=null}
    addMsg("请求失败: "+e.message,"bot");
  }
  isProcessing=false;
  input.placeholder="输入消息，/ 查看命令...";
  input.focus();
  refreshStatus();
}
async function refreshStatus(){
  try{
    const r=await fetch("/status");const j=await r.json();
    document.getElementById("st-sessions").textContent="会话: "+j.active_sessions;
    document.getElementById("st-uptime").textContent="运行: "+Math.round(j.uptime_seconds)+"s";
  }catch(e){}
}
// --- 斜杠命令 ---
let CMDS=[
  {name:"/help",  desc:"显示可用命令"},
  {name:"/clear", desc:"清空聊天显示"},
  {name:"/reset", desc:"重置当前会话"},
  {name:"/status",desc:"查看服务状态"},
  {name:"/user",  desc:"切换用户"},
];
// 启动时从后端加载 skill 列表
(async()=>{
  try{
    const r=await fetch("/skills");const skills=await r.json();
    skills.forEach(s=>{CMDS.push({name:"/"+s.name,desc:s.desc||"skill",isSkill:true})});
  }catch(e){}
})();
const cmdPanel=document.getElementById("cmd-panel");
let cmdIdx=-1,cmdFiltered=[];

function showCmds(filter){
  const q=filter.toLowerCase();
  cmdFiltered=CMDS.filter(c=>c.name.startsWith(q));
  if(!cmdFiltered.length){cmdPanel.style.display="none";return}
  cmdIdx=0;
  cmdPanel.innerHTML=cmdFiltered.map((c,i)=>
    '<div class="cmd-item'+(i===0?" active":"")+'" data-i="'+i+'">'
    +'<span class="cmd-name">'+c.name+'</span>'
    +'<span class="cmd-desc">'+c.desc+'</span></div>').join("");
  cmdPanel.style.display="block";
  cmdPanel.querySelectorAll(".cmd-item").forEach(el=>{
    el.onmouseenter=()=>{cmdIdx=+el.dataset.i;highlightCmd()};
    el.onclick=()=>{pickCmd()};
  });
}
function highlightCmd(){
  cmdPanel.querySelectorAll(".cmd-item").forEach((el,i)=>{
    el.classList.toggle("active",i===cmdIdx)});
}
function hideCmds(){cmdPanel.style.display="none";cmdIdx=-1;cmdFiltered=[]}
function pickCmd(){
  if(cmdIdx<0||!cmdFiltered.length)return;
  const cmd=cmdFiltered[cmdIdx].name;
  input.value="";input.style.height="42px";
  hideCmds();
  execCmd(cmd);
}
async function execCmd(cmd){
  if(cmd==="/help"){
    addSystem("可用命令: "+CMDS.map(c=>c.name+" — "+c.desc).join("\\n"));
  }else if(cmd==="/clear"){
    chat.innerHTML="";try{localStorage.removeItem(storageKey())}catch(e){}
    addSystem("聊天已清空（服务端会话仍保留）");
  }else if(cmd==="/reset"){
    await resetSession();
  }else if(cmd==="/status"){
    try{
      const r=await fetch("/status");const j=await r.json();
      addSystem("活跃会话: "+j.active_sessions+"  运行时间: "+Math.round(j.uptime_seconds)+"s");
    }catch(e){addSystem("获取状态失败")}
  }else if(cmd==="/user"){
    switchUser();
  }else{
    // skill 命令 — 发送给引擎执行
    const skill=CMDS.find(c=>c.name===cmd&&c.isSkill);
    if(skill){
      addSystem("Running skill: "+cmd);
      input.value=cmd;
      await send();
    }else{
      addSystem("未知命令: "+cmd);
    }
  }
}

sendBtn.onclick=send;
input.addEventListener("keydown",e=>{
  // 命令面板打开时拦截方向键和回车
  if(cmdPanel.style.display==="block"){
    if(e.key==="ArrowDown"){e.preventDefault();cmdIdx=Math.min(cmdIdx+1,cmdFiltered.length-1);highlightCmd();return}
    if(e.key==="ArrowUp"){e.preventDefault();cmdIdx=Math.max(cmdIdx-1,0);highlightCmd();return}
    if(e.key==="Enter"){e.preventDefault();pickCmd();return}
    if(e.key==="Escape"){e.preventDefault();hideCmds();return}
    if(e.key==="Tab"){e.preventDefault();pickCmd();return}
  }else{
    if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();send()}
  }
});
input.addEventListener("input",()=>{
  input.style.height="42px";input.style.height=input.scrollHeight+"px";
  const v=input.value;
  if(v.startsWith("/")&&!v.includes(" ")){showCmds(v)}else{hideCmds()}
});
// --- AskUser 轮询 ---
let askCardEl=null;
async function pollPendingQuestion(){
  try{
    const r=await fetch("/pending_question/"+userId);
    const j=await r.json();
    if(j.pending&&!askCardEl){showAskCard(j.question,j.options)}
    if(!j.pending&&askCardEl){askCardEl.remove();askCardEl=null}
  }catch(e){}
}
function showAskCard(question,options){
  askCardEl=document.createElement("div");
  askCardEl.className="ask-card";
  let html='<div class="ask-q">'+escHtml(question)+'</div>';
  if(options&&options.length){
    html+='<div class="ask-opts">';
    options.forEach((o,i)=>{html+='<button class="ask-opt" data-val="'+escHtml(o)+'">'+
      (i+1)+'. '+escHtml(o)+'</button>'});
    html+='</div>';
  }
  html+='<div class="ask-input-row"><input class="ask-input" placeholder="输入回答..."><button class="ask-submit">发送</button></div>';
  askCardEl.innerHTML=html;
  chat.appendChild(askCardEl);chat.scrollTop=chat.scrollHeight;
  askCardEl.querySelectorAll(".ask-opt").forEach(btn=>{
    btn.onclick=()=>submitAskAnswer(btn.dataset.val)});
  const askInput=askCardEl.querySelector(".ask-input");
  askCardEl.querySelector(".ask-submit").onclick=()=>{
    const v=askInput.value.trim();if(v)submitAskAnswer(v)};
  askInput.addEventListener("keydown",e=>{
    if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();
      const v=askInput.value.trim();if(v)submitAskAnswer(v)}});
}
function escHtml(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
async function submitAskAnswer(answer){
  if(!askCardEl)return;
  askCardEl.remove();askCardEl=null;
  addMsg(answer,"user");
  try{
    await fetch("/answer/"+userId,{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({user_id:userId,content:answer})});
  }catch(e){addMsg("提交回答失败: "+e.message,"bot")}
}
// --- 通知推送轮询 ---
async function pollNotifications(){
  try{
    const r=await fetch("/notifications/"+userId);
    const j=await r.json();
    if(j.notifications&&j.notifications.length){
      j.notifications.forEach(n=>{
        addMsg("["+n.source+"] "+n.text,"bot");
      });
    }
  }catch(e){}
}
loadHistory();
refreshStatus();setInterval(refreshStatus,5000);
setInterval(pollPendingQuestion,2000);
setInterval(pollNotifications,5000);
// 首次启动：自动触发引导，让 Brain 先开口
(async()=>{
  try{
    const r=await fetch("/status");const j=await r.json();
    if(j.first_boot&&!chat.children.length){
      isProcessing=true;
      input.placeholder="正在启动引导...";
      thinkingPanel=createThinkingPanel();
      chat.appendChild(thinkingPanel);chat.scrollTop=chat.scrollHeight;
      startThinkingPoll();
      const r2=await fetch("/message",{method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({user_id:userId,content:"[首次启动] 请按 BOOTSTRAP.md 的引导流程主动打招呼"})});
      const j2=await r2.json();
      stopThinkingPoll();
      collapseThinkingPanel(thinkingPanel);thinkingPanel=null;
      isProcessing=false;
      input.placeholder="输入消息，/ 查看命令...";
      addMsg(j2.text,"bot",j2.duration_ms);
      refreshStatus();
      return;
    }
  }catch(e){}
  if(!chat.children.length)addSystem("欢迎 "+userId+"，你的会话独立于其他用户。输入 / 查看可用命令");
})();
</script>
</body>
</html>
"""
