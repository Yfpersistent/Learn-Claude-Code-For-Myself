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



def agent_loop(messages):
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            max_tokens=8000,
        )
        message = response.choices[0].message
        # 模型没有调用工具，说明任务结束
        if not message.tool_calls:
            messages.append({
                "role": "assistant",
                "content": message.content
            })
            return
        # 保存 Assistant 这一轮（必须保存，否则下一轮上下文会丢失）
        messages.append(message)
        results = []
        for tool in message.tool_calls:
            args = json.loads(tool.function.arguments)
            handler = TOOL_HANDLERS[tool.function.name]
            if handler is None:
                raise ValueError(f"Unknown tool {tool.function.name}")
            try:
                output = handler(args)
            except Exception as e:
                output = str(e)
            results.append({
                "role": "tool",
                "tool_call_id": tool.id,
                "content": output
            })
        messages.extend(results)



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
        if history[-1]["role"] == "assistant":
            print(history[-1]["content"])