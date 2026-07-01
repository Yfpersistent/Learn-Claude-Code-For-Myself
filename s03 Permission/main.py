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


# first:hard deny
deny_list = ["/etc", "/root", "/var/log", "/bin", "/sbin", 
             "/usr/bin", "/usr/sbin", "/boot", "/dev", "/proc", "/sys",
             "rm -rf /", "sudo", "shutdown", "reboot", "> /dev/sda",
             "mkfs","dd if=",]
"""
"rm -rf /": Deny deleting the root directory, which would destroy the system.
"sudo": Deny using sudo to prevent privilege escalation.
"shutdown": Deny shutting down the system.
"reboot": Deny rebooting the system.
"> /dev/sda": Deny redirecting output to the main disk, which could overwrite
    the disk.
"mkfs": Deny formatting disks, which would destroy data.
"dd if=": Deny using dd to copy data from a disk, which could be used to overwrite or destroy data.
"""


def check_permission(command: str) -> str | None:
    # Check if the command is in the deny list
    for pattern in deny_list:
        if pattern in command:
            return f"Permission denied: Command '{command}' is restricted."
    return None

# second: soft ask
WORKDIR = Path(os.getcwd()).resolve() # 获取当前文件路径

PERMISSION_RULES = [
    {
        "tools": ["write_file", "edit_file"],
        "check": lambda args: not (WORKDIR / args.get("path", "")).resolve().is_relative_to(WORKDIR),
        "message": "Writing outside workspace",
    },
    {
        "tools": ["bash"],
        "check": lambda args: any(kw in args.get("command", "") for kw in ["rm ", "> /etc/", "chmod 777"]),
        "message": "Potentially destructive command",
    },
]
"""
PERMISSION_RULES本质上上一个list，每一个元素是一个dict，dict中有三个key：tools, check, message。
tools是一个list，表示哪些工具需要检查权限；check是一个函数，接受args参数，返回True或False，表示是否违反权限规则；message是一个字符串，表示违反权限规则的提示信息。
"""


def check_rules(tool_name: str, args: dict) -> str | None:
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return f"Permission denied: {rule['message']}."
    return None
"""
输入结构：args是工具参数
LLM根据用户输入根据工具定义生成结构化参数————Tool Calling
Tool Calling如何知道有哪些参数————已经定义好了
Tool Calling: 根据Tool Schema生成符合JSON规范的数据 
"""

# neither match
def ask_user(tool_name: str, args: dict, reason: str) -> str:
    print(f"Permission check failed for tool '{tool_name}' with arguments {args}. Reason: {reason}")
    while True:
        user_input = input("Do you want to allow this action? (yes/no): ").strip().lower()
        if user_input in ("yes", "y"):
            return "allow"
        elif user_input in ("no", "n"):
            return "deny"
        else:
            print("Invalid input. Please enter 'yes' or 'no'.")

# 将以上3中情况合成一个check函数
def check_permission_and_rules(name: str, args: dict) -> bool:
    if name == "bash":
        reason = check_permission(args.get("command", ""))
        if reason:
            print(f'\n {reason} is not allowed.')
            return False
    
    reason = check_permission(args.get("command", ""))
    if reason:
        decision = ask_user(name, args, reason)
        if decision == "deny":
            print(f"Action denied for tool '{name}' with arguments {args}.")
            return False
        return True
    return True


"""
block: 通常表示工具调用
block = ToolBlock(name="bash", input={"command": "rm -rf /"})
block.input = {"command": "rm -rf /"} --> 是一个dict，可以使用get
dict.get(key, default)方法获取值，如果key不存在则返回default
reason = check_permission(block.input.get("command", "")) 
等价于
input_data = block.input
command = input_data.get("command", "")
reason = check_permission(command)
"""


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
            # 增加检查权限功能
            if not check_permission_and_rules(tool.function.name, args):
                results.append({
                    "role": "tool",
                    "tool_call_id": tool.id,
                    "content": f"Permission denied for tool '{tool.function.name}' with arguments {args}."
                })
                continue


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