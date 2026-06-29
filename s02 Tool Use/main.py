import os
import subprocess
import json
from openai import OpenAI
from dotenv import load_dotenv


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
                    "command": {
                        "type": "string"
                    }
                },
                "required": ["command"]
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
            if tool.function.name == "bash":
                args = json.loads(tool.function.arguments)
                output = run_bash(args["command"])
                print(f"$ {args['command']}")
                print(output[:200])
                results.append({
                    "role": "tool",
                    "tool_call_id": tool.id,
                    "content": output,
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