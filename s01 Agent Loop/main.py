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


# Anthropic Tool格式
# TOOLS = [{"name": "bash", 
#           "description": "Run a shell command.",
#           "input_schema":{
#               "type":"object",
#               "properties":{"command": {"type": "string"}},
#               "required":['command'],
#           },
#           }]


# deepseek格式
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
    }
]


"""
[]:TOOLS可以有很多个工具，每一个{}是一个
{}:记录了每一个工具的具体属性，如名称、描述...
description: 记录了工具的描述信息，很重要，如果为Translate English to Chinese."那么该工具可能就不会调用工具
input_schema: 记录了工具的输入格式，type是输入参数的类型(bash需要command而不是filename、url)，
    properties表示Object里有哪些字段，这里必须是string，用{}因为未来可能不止"command"
"""

def run_bash(command:str) -> str: # 输入应该是str，如果输入run_bash(123)，也不会直接报错
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command detected. Command not executed."
    try:
        r = subprocess.run(command,                # r:接受返回值
                           shell=True,             # 将command当成shell执行，如果shell=False，则输入ls可能会查找名叫ls的文件
                           cwd=os.getcwd(),        # 获取当前工作目录
                           capture_output=True,    # 把命令输出抓回来，后续可以进一步告诉模型，如果为False则模型永远不知道输出的命令是什么
                           text=True,
                           timeout=120)
        out = (r.stdout + r.stderr).strip()   # 命令成功stdout，命令失败stdeer，无论成功还是失败都应该告诉模型
        return out[:50000] if out else "(no output.)" # token限制最多输出5000字符
    except subprocess.TimeoutExpired:    # 执行超过120秒
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:   # 将两种错误保存到e
        return f"Error: {e}"
    



# Anthropic 格式
# def agent_loop(messages):
#     while True:
#         response = client.message.create(
#             model = MODEL, system = SYSTEM, messages = messages, tools = TOOLS,
#             max_tokens = 8000,
#             )
#         messages.append({"role": "assistant", "content": response.message.content})

#         if response.stop_reason != "tool_use":  # type include: end_turn,tool_use,max_token,stop_sequence
#             return # 整个agent_loop的return

#         results = []
#         for block in response.content:
#             if block.type == "tool_use":
#                 output = run_bash(block.input["command"])
#                 results.append({
#                     "type": "tool_result",
#                     "tool_use_id": block.id,
#                     "content": output,
#                 })
#         messages.append({"role":"user","content":results})


# deepseek 格式
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