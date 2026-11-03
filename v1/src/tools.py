from dotenv import load_dotenv
import json
import os
import requests

load_dotenv(override=True)

# Pushover Setup

pushover_user = os.getenv("PUSHOVER_USER")
pushover_token = os.getenv("PUSHOVER_TOKEN")

pushover_url = "https://api.pushover.net/1/messages.json"

# Push message for pushover

def push(text):
    requests.post(
        pushover_url,
        data={
            "token": pushover_token,
            "user": pushover_user,
            "message": text,
        },
    )

# Quality inspection for improvement

# When users send in data and questions, reviewing the interactions allow for spotting areas of improvrement:
# - Considerations for additional information provision to the system
# - Finding points of weakness or inefficiency in the solution
# Function that pushes notification to Pushover with user details

def record_user_details(email, name='Name not provided', notes=None):
    push(f"Recording interst from {name} with email {email} and notes notes {notes}")
    return "OK"

# Pushing questions that couldn't be answered due to lack of information to Pushover for inspection

def record_unknown_question(question):
    push(f"{question} was asked and I couldn't answer")
    return "OK"

# Tool-setting

record_user_details_json = {
    "name": "record_user_details",
    "description": "Use this tool to record that a user is interested in being in touch and provide an email address",
    "parameters": {
        "type":"object",
        "properties": {
            "email": {"type":"string", "description": "The user's email address"},
            "name": {"type":"string", "description": "The user's name if it was provided"},
            "notes": {"type":"string", "description": "Any additional info about the conversation that's worth recording to give context"}
        },
        "required":["email"],
        "additionalProperties": False
    }
}

record_unknown_question_json = {
    "name": "record_unknown_question",
    "description": "Always use this too to record any question that couldn't be answered as you didn't know the answer",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {"type":"string", "description": "the question that coud not be answered"},
        },
        "required": ["question"],
        "additionalProperties": False
    }
}

tools = [{"type":"function", "function": record_user_details_json},
        {"type":"function", "function": record_unknown_question_json}]

tool_map = {
    "record_user_details": record_user_details,
    "record_unknown_question": record_unknown_question,
}

# Handling the tool calls with a function

def handle_tool_calls(tool_calls):
    results = []
    for tool_call in tool_calls:
        tool_name = tool_call.function.name
        arguments = json.loads(tool_call.function.arguments)
        print(f"Tool called: {tool_name}", flush=True)
        tool = tool_map.get(tool_name)
        result = tool(**arguments) if tool else "Unknown tool: " + tool_name
        results.append(
            {'role': 'tool', 'content': json.dumps(result),"tool_call_id": tool_call.id}
        )
    return results