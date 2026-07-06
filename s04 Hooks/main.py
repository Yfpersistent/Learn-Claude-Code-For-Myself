import os
import subprocess
import json
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path


load_dotenv(override=True)
client = OpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url=os.getenv("BASE_URL"))



MODEL = "deepseek-chat"
SYSTEM = "You are a helpful assistant."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path of the file to read."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path of the file to write."
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write into the file."
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace text in file once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path of the file to edit."
                    },
                    "old_text": {
                        "type": "string",
                        "description": "The text to be replaced."
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The new text to replace with."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files by pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Find the file by global pattern."
                    }
                },
                "required": ["pattern"]
            }
        }
    }
]


HOOKS = {
    "UserPromptSubmit":[],
    "PreToolUse":[],
    "PostToolUse":[],
    "Stop":[]
}
# 将所有可能发生的事件分成4大类，HOOKS对应的是“事件名”->对应的函数列表
# ============================================================================================
# | 序号 |        名称       |          触发时机          |                 典型用途           |
# |------|------------------|----------------------------|-----------------------------------|
# |  1   | UserPromptSubmit | 用户输入提交后、进入 LLM 前  | 输入验证、注入上下文                |
# |  2   | PreToolUse       | 工具执行前                  | 权限检查、日志记录                  |
# |  3   | PostToolUse      | 工具执行后                  | 副作用（自动 git add 等）、输出检查  |
# |  4   | Stop             | 循环即将退出时              | 收尾清理（CC 还支持强制续跑）        |
# ============================================================================================




def register_hook(event: str, func):
    if event in HOOKS:
        HOOKS[event].append(func)
    else:
        raise ValueError(f"Unknown event: {event}")
# 将某个函数加入该事件的函数列表中（把功能插入到事件槽位中）


def trigger_hooks(event: str, *args):  # event:HOOKS中哪一个阶段；*args:不确定参数的个数，全部传给hooks
    for func in HOOKS[event]:  # 遍历该event中的所有函数
        result = func(*args)
        if result is not None:  # 中断机制：如果hooks返回了非None的值，说明正在拦截流程
            return result
    return None
# PreToolUse 的非 None 返回值会阻止本次工具执行，Stop 的非 None 返回值会强制续跑；
# UserPromptSubmit 和 PostToolUse 的返回值未被使用
"""
思维误区：HOOKS并没有分类的想法（if event == "UserPromptSubmit": elif event == "PreToolUse": ...），trigger_hooks()
并没有分类的逻辑，甚至trigger_hooks()根本不知道有哪些events，只是遍历该event的所有函数，执行它们，并返回第一个非None的结果。
也就是说，HOOKS是一个事件驱动的机制，而不是一个分类机制。
"""
# Step1:UserPromptSubmit Hook: 观测用户输入的prompt，决定是否修改或拦截
WORKDIR = r"D:\Learn Claude Code on GitHub\s04 Hooks"
def check_inject_hook(query: str) -> str | None:
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None   # return None = no modification, let prompt through
# check_inject_hook() 只做观测，不改写输入的prompt



# Step2:PreToolUse Hook
# Part1: 权限检查
deny_list = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
def permission_check_hook(block) -> str | None:
    if block.name == "bash":
        for pattern in deny_list:
            if pattern in block.input.get("command", ""):
                return f"Error: Dangerous command detected. Command not executed."
    elif block.name in ["read_file", "write_file", "edit_file"]:
        path = block.input.get("path", "")
        if not (WORKDIR / path).resolve().is_relative_to(Path(WORKDIR).resolve()):
            """(WORKDIR / path).resolve()：将WORKDIR和path拼接成一个完整路径，并解析为绝对路径"""
            choice = input("Allow?[y/N]").strip().lower()
            if choice not in ["y", "yes"]:
                return "Permission denied by user."
    return None
# Part2: 日志记录
def log_hook(block):
    print(f"[HOOKS]{block.name}(...)")
    return None
# Part3: 大文件提醒
def large_output_hook(block,output):
    if len(str(output)) > 10000:
        print(f"[HOOKS] Large output from {block.name}.")
    return None

# Step3:PostToolUse Hook


# step4:Stop Hook
"""
messages = [
    {"role":"user",
    "context":"hello!"},
    {"role":"assistant",
    "context":[
        {"type":"text",
        "text":"I'll be back.",
        {"type":"tool_result",
        "content":"..."}
    ]},
    {...}
]
"""
def summary_hook(messages:list) -> str | None:
    tool_result = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tool_result += 1
    print(f"[HOOKS] Summary: {len(messages)} messages, {tool_result} tool calls.")
    return None


def run_bash(command:str) -> str: 
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command detected. Command not executed."
    try:
        r = subprocess.run(command,
                           shell=True,
                           cwd=os.getcwd(),
                           capture_output=True,
                           text=True,
                           timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output.)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

def run_read_file(path,limit = None):
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if limit:
        lines = lines[:limit]
    return "\n".join(lines)


def run_write_file(path, content):
    path.write_text(content, encoding="utf-8", errors="ignore")
    return f"Wrote {len(content.encode('utf-8'))} bytes to {path}."


def run_edit_file(path, old_text, new_text):
    content = path.read_text(encoding="utf-8", errors="ignore")
    if old_text not in content:
        return f"Error: '{old_text}' not found in {path}."
    new_content = content.replace(old_text, new_text, 1) # new_content is different from new_text
    path.write_text(new_content, encoding="utf-8", errors="ignore")
    return f"Replaced first occurrence of '{old_text}' with '{new_text}' in {path}."


def run_glob(pattern):
    files = list(Path(".").rglob(pattern))
    if not files:
        return f"No files found matching pattern '{pattern}'."
    return "\n".join(str(f) for f in files)

# 工具分发
TOOL_HANDLERS = {
    "bash": lambda args: run_bash(args["command"]),
    "read_file": lambda args: run_read_file(Path(args["path"])),
    "write_file": lambda args: run_write_file(Path(args["path"]), args["content"]),
    "edit_file": lambda args: run_edit_file(Path(args["path"]), args.get("old_text", ""), args.get("new_text", "")),
    "glob": lambda args: run_glob(args["pattern"]),
}

from types import SimpleNamespace

def agent_loop(messages):
    while True:

        # 1. 调用 LLM
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            max_tokens=5000,
            temperature=0.2
        )

        message = response.choices[0].message

        # 2. 模型没有调用工具，说明任务结束
        if not message.tool_calls:

            messages.append({
                "role": "assistant",
                "content": message.content
            })

            trigger_hooks("Stop", messages)

            return

        # 3. 保存 assistant 的 tool_call
        messages.append(message)

        tool_results = []

        # 4. 执行工具
        for tool in message.tool_calls:

            args = json.loads(tool.function.arguments)

            # 构造 block，供 Hook 使用
            block = SimpleNamespace(
                name=tool.function.name,
                input=args
            )

            # ---------- PreToolUse ----------
            reason = trigger_hooks("PreToolUse", block)

            if reason is not None:
                output = reason

            else:
                handler = TOOL_HANDLERS.get(block.name)

                if handler is None:
                    output = f"Unknown tool: {block.name}"

                else:
                    try:
                        output = handler(args)
                    except Exception as e:
                        output = str(e)

            # ---------- PostToolUse ----------
            trigger_hooks(
                "PostToolUse",
                block,
                output
            )

            tool_results.append({
                "role": "tool",
                "tool_call_id": tool.id,
                "content": output
            })

        # 5. 保存所有 Tool 输出
        messages.extend(tool_results)


register_hook("UserPromptSubmit", check_inject_hook)

if __name__ == "__main__":
    history = [
        {
            "role": "system",
            "content": SYSTEM
        }
    ]
    while True:
        query = input(">>> ")
        if query.lower() in ("q", "quit", "exit"):
            break
        history.append({
            "role": "user",
            "content": query
        })
        agent_loop(history)
        last = history[-1]
        if hasattr(last, "role"):
            if last.role == "assistant":
                print(last.content)