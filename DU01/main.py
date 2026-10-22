import json
import os
import time
import platform
import traceback
import uuid
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))
endpoint = f"https://api.runpod.ai/v2/{os.getenv('RUNPOD_ENDPOINT_ID', '')}/runsync"
headers = {"Authorization": f"Bearer {os.getenv('RUNPOD_API_KEY', '')}"}

run_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
run_started_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
run_started = time.monotonic()
log_events = []
model_calls = []
tool_results = []
current_step = 0


def redact(value):
    # JSON round trip also snapshots mutable messages before the next step changes them.
    text = json.dumps(value, ensure_ascii=False, default=str)
    key = os.getenv("RUNPOD_API_KEY")
    if key:
        text = text.replace(json.dumps(key)[1:-1], "[REDACTED]")
    return json.loads(text)


def log(step, data=None, level="INFO"):
    event = redact({
        "sequence": len(log_events) + 1,
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "elapsed_s": round(time.monotonic() - run_started, 3),
        "level": level,
        "step": current_step,
        "message": step,
        "data": data,
    })
    log_events.append(event)
    print(format_event(event), flush=True)


def format_event(event):
    text = (f"\n[{event['timestamp']}] +{event['elapsed_s']:.3f}s "
            f"[{event['level']}] [krok {event['step']}] {event['message']}")
    if event["data"] is not None:
        text += "\n" + json.dumps(event["data"], ensure_ascii=False, indent=2)
    return text


def save_logs(status):
    directory = Path(__file__).parent / "logs"
    directory.mkdir(exist_ok=True)
    base = directory / run_id
    token_totals = {
        name: sum((call.get("usage") or {}).get(name, 0) or 0 for call in model_calls)
        for name in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    summary = redact({
        "run_id": run_id,
        "status": status,
        "started_at": run_started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "duration_s": round(time.monotonic() - run_started, 3),
        "endpoint": endpoint,
        "python": platform.python_version(),
        "steps": current_step,
        "model_calls": model_calls,
        "tool_results": tool_results,
        "tokens": token_totals,
        "event_count": len(log_events),
    })
    base.with_suffix(".json").write_text(
        json.dumps({"summary": summary, "events": log_events}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    base.with_suffix(".log").write_text(
        "SHRNUTÍ BĚHU\n" + json.dumps(summary, ensure_ascii=False, indent=2)
        + "\n\nČASOVÁ OSA\n" + "\n".join(format_event(event) for event in log_events),
        encoding="utf-8",
    )
    print(f"\nLogy uloženy:\n  {base.with_suffix('.log')}\n  {base.with_suffix('.json')}", flush=True)


def read_response(response):
    log(f"HTTP {response.status_code}", {
        "duration_s": response.elapsed.total_seconds(),
        "response_bytes": len(response.content),
    })
    if not response.ok:
        log("Chybová odpověď serveru", response.text)
    response.raise_for_status()
    job = response.json()
    log("Odpověď Runpod API", job)
    return job


def add(a, b):
    return a + b


def ask_model(messages):
    started = time.monotonic()
    log("Historie konverzace před voláním modelu", messages)
    payload = {"input": {
        "openai_route": "/v1/chat/completions",
        "openai_input": {
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 512,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    }}
    log(f"POST {endpoint} — odesílám požadavek (limit HTTP 300 s)", payload)
    response = requests.post(
        endpoint,
        headers=headers,
        json=payload,
        timeout=300,
    )
    job = read_response(response)
    deadline = time.monotonic() + 300
    while job["status"] in ("IN_QUEUE", "IN_PROGRESS"):
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Runpod job {job['id']} is still {job['status']}")
        log(f"Úloha {job['id']}: {job['status']} — čekám 2 s")
        time.sleep(2)
        status_url = endpoint.rsplit("/", 1)[0] + "/status/" + job["id"]
        log(f"GET {status_url}")
        response = requests.get(
            status_url,
            headers=headers,
            timeout=30,
        )
        job = read_response(response)
    if job["status"] != "COMPLETED":
        raise RuntimeError(f"Runpod job failed: {job}")
    completion = job["output"][0]
    message = completion["choices"][0]["message"]
    model_calls.append({
        "step": current_step,
        "job_id": job["id"],
        "worker_id": job.get("workerId"),
        "model": completion.get("model"),
        "duration_s": round(time.monotonic() - started, 3),
        "queue_ms": job.get("delayTime"),
        "execution_ms": job.get("executionTime"),
        "finish_reason": completion["choices"][0].get("finish_reason"),
        "usage": completion.get("usage"),
    })
    log(f"Volání modelu dokončeno za {time.monotonic() - started:.2f} s", {
        "worker": job.get("workerId"),
        "delayTime_ms": job.get("delayTime"),
        "executionTime_ms": job.get("executionTime"),
        "message": message,
    })
    return message


def multiply(a, b):
    return a * b


# The model sees only the descriptions and JSON schemas, not the Python code.
available_functions = {"add": add, "multiply": multiply}
tools = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"},
                },
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        },
    }
    for name, description in [
        ("add", "Add two numbers and return their sum."),
        ("multiply", "Multiply two numbers and return their product."),
    ]
]


def main():
    global current_step
    if not os.getenv("RUNPOD_ENDPOINT_ID") or not os.getenv("RUNPOD_API_KEY"):
        raise ValueError("V .env chybí RUNPOD_ENDPOINT_ID nebo RUNPOD_API_KEY")
    messages = [
        {"role": "system", "content": (
            "You are a helpful assistant. Use the available tools for arithmetic. "
            "After receiving the tool result, explain the answer naturally in Czech."
        )},
        {"role": "user", "content": "Kolik je 123 + 456?"},
    ]
    log("Start kalkulačky — nativní tool calling", {
        "endpoint": endpoint, "max_steps": 5, "tools": tools,
        "question": messages[-1]["content"],
    })
    for step in range(1, 6):
        current_step = step
        log(f"KROK {step}/5 — volání modelu (tool_choice=auto)")
        message = ask_model(messages)
        calls = message.get("tool_calls") or []
        assistant_message = {"role": "assistant", "content": message.get("content")}
        if calls:
            assistant_message["tool_calls"] = calls
        messages.append(assistant_message)
        if not calls:
            if not message.get("content"):
                raise RuntimeError(
                    "Model returned neither content nor tool_calls. "
                    "Check the endpoint's tool-call and reasoning parser configuration."
                )
            log("HOTOVO — lidsky čitelná odpověď LLM", message["content"])
            return
        log("LLM vybralo nástroje — skutečné API tool_calls", calls)
        for call in calls:
            name = call["function"]["name"]
            arguments = json.loads(call["function"]["arguments"])
            if name not in available_functions:
                raise ValueError(f"Unknown tool: {name}")
            log(f"Python spouští {name}", arguments)
            tool_started = time.monotonic()
            result = available_functions[name](**arguments)
            tool_results.append({
                "step": step, "tool_call_id": call["id"], "name": name,
                "arguments": arguments, "result": result,
                "duration_ms": round((time.monotonic() - tool_started) * 1000, 3),
            })
            log(f"Výsledek Python funkce {name}", result)
            tool_message = {
                "role": "tool",
                "tool_call_id": call["id"],
                "name": name,
                "content": json.dumps({"result": result}, ensure_ascii=False),
            }
            messages.append(tool_message)
            log("Výsledek vracím LLM jako skutečnou zprávu role=tool", tool_message)
    raise RuntimeError("Tool-call limit reached without a final answer")


if __name__ == "__main__":
    status = "ERROR"
    try:
        log("Zahajuji běh", {"run_id": run_id})
        main()
        status = "SUCCESS"
    except KeyboardInterrupt:
        status = "INTERRUPTED"
        log("Běh přerušen uživatelem", level="WARNING")
        raise
    except Exception as error:
        log("Běh skončil chybou", {
            "type": type(error).__name__, "message": str(error),
            "traceback": traceback.format_exc(),
        }, level="ERROR")
        raise
    finally:
        log("Konec běhu", {"status": status})
        save_logs(status)
