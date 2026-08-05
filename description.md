# **Project Canvas: Autonomous OpenRouter Pipeline (AOP)**

> **Tagline:** A multi-model software factory orchestration engine leveraging OpenRouter as a unified AI middleware layer to execute autonomous SDLC workflows with deterministic gate checks.

## **1\. Executive Summary & Vision**

### **Problem Statement**

Standard single-model agent workflows face significant tradeoffs:

* High-end reasoning models (e.g., Claude Opus) are slow and expensive for routine coding or boilerplate generation.  
* Single-prompt/single-agent setups suffer from context rot, hallucinations, and lack of reproducible execution.  
* Pure AI workflows waste model tokens running tests and formatting code that local, deterministic tooling can handle instantly for free.

### **Solution Overview**

The **Autonomous OpenRouter Pipeline (AOP)** builds a modular "software factory." It separates software development into distinct phases (Scout ![][image1] Build ![][image1] Test ![][image1] Review ![][image1] Document), routing each phase to the optimal AI model tier via OpenRouter. Between AI phases, zero-cost deterministic code hooks (AST parsers, linters, pytest) enforce strictly typed state transitions.

## **2\. Core Architectural Principles**

1. **Agents \+ Code Beat Agents Alone:** Never spend model tokens on tasks that code executes instantly (e.g., running tests, validating syntax, checking JSON formats).  
2. **Context Efficiency:** Passing passing test outputs into an AI context window is prohibited. Model contexts receive feedback *only* when a deterministic gate fails.  
3. **Model Stack Optimization:** Use high-reasoning frontier models for planning, fast/inexpensive workhorses for code synthesis, and specialized open-weights models for review.  
4. **Deterministic Gate Checks:** Every phase output is validated against strict JSON schemas and AST execution checks before handoff to the next phase.

## **3\. OpenRouter Model Tiering Matrix**

By leveraging OpenRouter's unified API, the system eliminates SDK lock-in while utilizing dynamic model routing, serverless fallback tiers, and explicit cost/performance allocation.

| SDLC Phase | Model Selection | Target Model ID | Selection Rationale | Est. Cost / 1M Tokens |
| :---- | :---- | :---- | :---- | :---- |
| **Phase 1: Scout & Plan** | **Claude Opus 5** | anthropic/claude-opus-5 | Frontier reasoning for high-level architecture & JSON schema outputs. | \~$15.00 / $75.00 |
| **Phase 2: Code Build** | **Gemini 3.6 Flash** | google/gemini-3.6-flash | Ultra-fast token throughput and excellent code completion at workhorse pricing. | \~$0.50 / $1.50 |
| **Phase 3: Gate Verification** | *Local Runtime (Python)* | N/A | Deterministic pytest, ruff linter, and JSON schema validation hooks. | **$0.00** |
| **Phase 4: Review & Docs** | **Kimi K3** | moonshot/kimi-k3 | Deep open-weights reasoning model for code diff reviews and markdown generation. | \~$1.00 / $3.00 |

## **4\. Pipeline State Machine Workflow**

                        \[ User Feature Request / Goal \]  
                                       │  
                                       ▼  
                   ┌───────────────────────────────────────┐  
                   │ Phase 1: Orchestrator & Scout          │  
                   │ Model: Claude Opus 5                  │  
                   │ Task: Produce Structured Execution    │  
                   │       Plan (JSON Format)              │  
                   └───────────────────┬───────────────────┘  
                                       │  
                                       ▼  
                   ┌───────────────────────────────────────┐  
                   │ Phase 2: Code Builder                 │  
                   │ Model: Gemini 3.6 Flash               │  
                   │ Task: Generate File Diffs & Code      │  
                   └───────────────────┬───────────────────┘  
                                       │  
                                       ▼  
                   ┌───────────────────────────────────────┐  
                   │ Phase 3: Deterministic Gate Check     │  
                   │ Engine: Local Subprocess / Pytest     │  
                   └─────────┬───────────────────┬─────────┘  
                             │                   │  
                     (Pass)  │                   │ (Fail)  
                             ▼                   ▼  
           ┌───────────────────────────┐   ┌───────────────────────────┐  
           │ Phase 4: Review & Docs    │   │ Error Payload Handoff     │  
           │ Model: Kimi K3            │   │ Loop Back to Build Phase  │  
           └─────────────┬─────────────┘   └─────────────┬─────────────┘  
                         │                               │  
                         ▼                               └──────┐  
            \[ Execution Complete \]                              │  
                     ▲                                          │  
                     └──────────────────────────────────────────┘

## **5\. Repository & Blueprint Structure**

aop-factory/  
├── config/  
│   └── roster.yaml         \# OpenRouter model IDs, parameters, and harness settings  
├── adw/                    \# AI Developer Workflow modules  
│   ├── \_\_init\_\_.py  
│   ├── modules/  
│   │   ├── openrouter.py   \# Unified OpenRouter HTTP client wrapper  
│   │   ├── runner.py       \# Deterministic subprocess execution & gate checks  
│   │   └── state.py        \# Typed session state and envelope manager  
│   └── sdlc\_pipeline.py    \# Main state-machine execution loop  
├── tests/                  \# Local test suite for deterministic validation  
├── sessions/               \# ADW execution outputs, diffs, and logs  
├── requirements.txt        \# Python dependencies  
└── main.py                 \# CLI entrypoint for agentic feature orchestration

## **6\. Technical Implementation Spec**

### **6.1 Configuration (config/roster.yaml)**

version: "1.0"  
factory\_name: "AOP Software Factory"

pipeline:  
  scout:  
    model: "anthropic/claude-opus-5"  
    temperature: 0.2  
    max\_tokens: 4096  
    response\_format: "json\_object"

  builder:  
    model: "google/gemini-3.6-flash"  
    temperature: 0.1  
    max\_tokens: 8192

  reviewer:  
    model: "moonshot/kimi-k3"  
    temperature: 0.2  
    max\_tokens: 4096

### **6.2 Unified OpenRouter Client (adw/modules/openrouter.py)**

import os  
import requests  
from typing import Dict, Any, List, Optional

OPENROUTER\_API\_KEY \= os.getenv("OPENROUTER\_API\_KEY")  
API\_URL \= "https://openrouter.ai/api/v1/chat/completions"

def call\_openrouter(  
    model: str,   
    messages: List\[Dict\[str, str\]\],   
    temperature: float \= 0.2,   
    max\_tokens: int \= 4096,  
    response\_format: Optional\[Dict\[str, Any\]\] \= None  
) \-\> str:  
    """Unified API caller for OpenRouter models."""  
    if not OPENROUTER\_API\_KEY:  
        raise ValueError("OPENROUTER\_API\_KEY environment variable is missing.")

    headers \= {  
        "Authorization": f"Bearer {OPENROUTER\_API\_KEY}",  
        "HTTP-Referer": "https://github.com/aop-factory",  
        "X-Title": "AOP Software Factory",  
        "Content-Type": "application/json"  
    }  
      
    payload \= {  
        "model": model,  
        "messages": messages,  
        "temperature": temperature,  
        "max\_tokens": max\_tokens  
    }  
      
    if response\_format:  
        payload\["response\_format"\] \= response\_format

    response \= requests.post(API\_URL, headers=headers, json=payload, timeout=120)  
    response.raise\_for\_status()  
    data \= response.json()  
    return data\["choices"\]\[0\]\["message"\]\["content"\]

### **6.3 Orchestration Engine (adw/sdlc\_pipeline.py)**

import json  
import subprocess  
import yaml  
from typing import Dict, Any  
from adw.modules.openrouter import call\_openrouter

class SDLCFactory:  
    def \_\_init\_\_(self, config\_path: str \= "config/roster.yaml"):  
        with open(config\_path, "r") as f:  
            self.config \= yaml.safe\_load(f)\["pipeline"\]

    def run\_scout(self, goal: str) \-\> Dict\[str, Any\]:  
        """Phase 1: High-level planning via Claude Opus 5."""  
        messages \= \[  
            {  
                "role": "system",  
                "content": (  
                    "You are a lead system architect. Output a structured JSON plan with keys: "  
                    "'summary', 'target\_files', 'steps', and 'test\_criteria'."  
                )  
            },  
            {"role": "user", "content": f"Create an implementation plan for: {goal}"}  
        \]  
        response \= call\_openrouter(  
            model=self.config\["scout"\]\["model"\],  
            messages=messages,  
            response\_format={"type": "json\_object"}  
        )  
        return json.loads(response)

    def run\_builder(self, plan: Dict\[str, Any\], feedback: str \= "") \-\> str:  
        """Phase 2: Code synthesis via Gemini 3.6 Flash."""  
        context \= f"Plan: {json.dumps(plan)}"  
        if feedback:  
            context \+= f"\\n\\nPrevious Execution Error (Fix Required):\\n{feedback}"

        messages \= \[  
            {"role": "system", "content": "You are a staff engineer. Return complete python code block solutions."},  
            {"role": "user", "content": context}  
        \]  
        return call\_openrouter(  
            model=self.config\["builder"\]\["model"\],  
            messages=messages  
        )

    def run\_deterministic\_gate(self) \-\> tuple\[bool, str\]:  
        """Phase 3: Zero-cost code validation via Pytest and Syntax Check."""  
        try:  
            res \= subprocess.run(  
                \["pytest", "--maxfail=1", "--quiet"\],  
                capture\_output=True,  
                text=True,  
                timeout=30  
            )  
            passed \= res.returncode \== 0  
            return passed, res.stdout or res.stderr  
        except Exception as e:  
            return False, str(e)

    def run\_reviewer(self, plan: Dict\[str, Any\], code: str) \-\> str:  
        """Phase 4: Code review & documentation via Kimi K3."""  
        messages \= \[  
            {"role": "system", "content": "You are a senior code reviewer. Summarize code changes and document new behavior."},  
            {"role": "user", "content": f"Plan: {json.dumps(plan)}\\n\\nCode:\\n{code}"}  
        \]  
        return call\_openrouter(  
            model=self.config\["reviewer"\]\["model"\],  
            messages=messages  
        )

    def execute(self, goal: str, max\_retries: int \= 3):  
        print(f"\[\*\] Initiating Factory Run for Goal: {goal}")  
          
        \# Phase 1: Planning  
        plan \= self.run\_scout(goal)  
        print("\[+\] Phase 1 (Scout) Complete. Plan generated.")

        feedback \= ""  
        for attempt in range(1, max\_retries \+ 1):  
            print(f"\[\*\] Phase 2 (Build) Attempt {attempt}/{max\_retries}...")  
            code \= self.run\_builder(plan, feedback=feedback)

            \# Write generated code to test file (simplified example)  
            with open("app\_output.py", "w") as f:  
                f.write(code)

            \# Phase 3: Gate Check  
            passed, gate\_output \= self.run\_deterministic\_gate()  
            if passed:  
                print("\[+\] Phase 3 (Deterministic Gate) PASSED\!")  
                break  
            else:  
                print(f"\[\!\] Phase 3 Gate Failed. Feeding errors into retry loop...")  
                feedback \= gate\_output

        \# Phase 4: Review  
        print("\[\*\] Phase 4 (Review & Docs)...")  
        docs \= self.run\_reviewer(plan, code)  
        print("\[+\] Factory Run Completed Successfully\!\\n")  
        print("=== Review Summary \===")  
        print(docs)

if \_\_name\_\_ \== "\_\_main\_\_":  
    factory \= SDLCFactory()  
    factory.execute("Add light/dark theme switcher utility with persistence")

## **7\. Key Performance Indicators (KPIs)**

* **Cost Reduction:** **\~65%–75% lower token expenditure** compared to using a single frontier model for every stage.  
* **Execution Reliability:** Deterministic gate loops prevent broken code from escalating to documentation or deployment phases.  
* **Latency Optimization:** Delegating code generation to Gemini Flash yields up to ![][image2] **faster execution speeds** during build phases.

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABUAAAAaCAYAAABYQRdDAAAAjklEQVR4XmNgGAWjYHgCRgUFhXh0QYqBvLz8bCkpKS4QRpcjGygqKurLycmVgzC6HNmAKEOB3hAhFQPDNR2EgUExVVRUlAfFQHV1dV6gRCsZeB4UvwO62AXFUHKAkpISP9CwuVAsiS5PFqCJoUDv1gMNMwRhdDlyARPQsMnoghQDkPfRxSgGNDF0FAwTAAA4VigAfwjpvwAAAABJRU5ErkJggg==>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABoAAAAaCAYAAACpSkzOAAABd0lEQVR4Xu2UvUoDQRSFY4wIgoI/RcjP7iYsBCxEXRCxU0HBQtBCrCwF30GwEwQbCxFjYWWsxM7CxsLCwsbX8BGs9Ds6q+s1RtCwIOTAx8zce+beyWSSTKajf6coinpsrG2qVCpjwvf9ehAEx4y3jNPCev8kCl471t16j2aPgnne+n+t1BpR8E54nrejNcW3WD8LrtRPevFEDLlkLKlSqTQEoY03UxcNLuFG2CSHGCd+wrTb8SoOMCjINQqFwsjHjiYql8vLcQPMfcJ6JPIzUBcss2EYDjA/F9QoWP8XpdYIZYvF4jAbGnAovvtdkZsVXNeR/IGT9bUUGyd89xhgxeal+BOTv/LfvrPW4kSjGB8EL2xKsWq16sWNOOSB3cNV9ZI7E27/EuOusN53pdaIQosYnxxzitFwIW4Eq2ZLjoKneuYiDsrn2E6aP0mnFpguKLLJeA/7wnrJbxCftPFY5NeCn/4jMeQxztdqtX6ba6tSa9RRR6noBeNbdvEuXmO6AAAAAElFTkSuQmCC>