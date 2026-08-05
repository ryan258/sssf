# **Functional & Technical Specification: Autonomous OpenRouter Pipeline (AOP)**

> **Document Version:** 1.0.0

> **Status:** Draft / Approved

> **Target Audience:** Core AI Engineers, Systems Architects, and SDLC Tool Developers

## **1\. Overview & System Scope**

The **Autonomous OpenRouter Pipeline (AOP)** is an automated software factory engine designed to execute complex software development lifecycle (SDLC) tasks without manual intervention. By leveraging **OpenRouter** as a unified AI middleware layer, AOP dynamically routes tasks to optimal LLM models (ranging from high-reasoning frontier models to lightweight workhorses) while enforcing zero-cost deterministic gate checks (linters, static analyzers, and test suites) between model transitions.

### **1.1 Goals & Key Objectives**

* **Model Diversity & Tiering:** Eliminate provider lock-in by accessing models via OpenRouter (e.g., Anthropic Claude, Google Gemini, Moonshot Kimi) using unified API contracts.  
* **Deterministic Verification:** Ensure no AI-generated code advances to subsequent phases without passing local syntax, linting, and automated test gates.  
* **Context Budget Optimization:** Prevent token waste by withholding passing test outputs from model prompts, feeding error logs into model context windows *only* when a deterministic gate check fails.  
* **Reproducible Execution:** Standardize inter-phase communication using strictly typed JSON envelopes validated against JSON Schemas.

## **2\. Architecture & Data Flow**

### **2.1 State Machine Lifecycle**

               ┌──────────────────────────────┐  
               │    User Request / Goal       │  
               └──────────────┬───────────────┘  
                              │  
                              ▼  
               ┌──────────────────────────────┐  
               │    Phase 1: Scout & Plan     │  
               │   (Claude Opus 5 via OR)     │  
               └──────────────┬───────────────┘  
                              │  
                              ▼  
               ┌──────────────────────────────┐  
               │  Gate 1: JSON Schema Check   │ ──(Fail)──► \[ Abort / Prompt Retry \]  
               └──────────────┬───────────────┘  
                              │ (Pass)  
                              ▼  
         ┌──────────────────────────────────────────┐  
         │          Phase 2: Code Builder           │◄────────────────────────┐  
         │       (Gemini 3.6 Flash via OR)          │                         │  
         └────────────────────┬─────────────────────┘                         │  
                              │                                               │  
                              ▼                                               │  
         ┌──────────────────────────────────────────┐                         │  
         │  Gate 2: Local Deterministic Run         │                         │  
         │  \- AST Syntax Check (\`ast.parse\`)        │                         │  
         │  \- Linter Check (\`ruff\`)                 │                         │  
         │  \- Automated Tests (\`pytest\`)            │                         │  
         └─────────────┬────────────────────────────┘                         │  
                       │                                                      │  
             (Pass)    │                    (Fail: Retry Count \< Max)          │  
                       ├──────────────────────────────────────────────────────┘  
                       │                    Feedback Envelope (Error Payload)  
                       ▼  
         ┌──────────────────────────────────────────┐  
         │      Phase 3: Code Review & Docs         │  
         │         (Kimi K3 via OR)                 │  
         └────────────────────┬─────────────────────┘  
                              │  
                              ▼  
               ┌──────────────────────────────┐  
               │     Final Artifacts Written  │  
               └──────────────────────────────┘

## **3\. Core Component Specifications**

### **3.1 OpenRouter Middleware Client (adw/modules/openrouter.py)**

The middleware client wraps HTTP POST requests sent to https://openrouter.ai/api/v1/chat/completions.

#### **Key Responsibilities**

* **Header Management:** Include mandatory OpenRouter identification headers (HTTP-Referer, X-Title).  
* **Fallback Strategy:** Support primary and secondary model endpoints if the target provider returns standard HTTP errors (e.g., 429 Too Many Requests, 502 Bad Gateway, 503 Service Unavailable).  
* **Structured Output Enforcement:** Support response\_format: {"type": "json\_object"} or JSON Schema mode for model endpoints that support structured outputs.

#### **Request Payload Schema**

{  
  "model": "string",  
  "messages": \[  
    {  
      "role": "system | user | assistant",  
      "content": "string"  
    }  
  \],  
  "temperature": 0.2,  
  "max\_tokens": 4096,  
  "response\_format": { "type": "json\_object" }  
}

### **3.2 State Envelope Manager (adw/modules/state.py)**

Inter-agent communication relies on state envelopes saved inside the session workspace (sessions/{session\_id}/).

#### **Session Directory Layout**

sessions/  
└── adw\_20260805\_114500/  
    ├── session.json             \# Root state payload  
    ├── phase\_1\_plan.json        \# Scout output artifact  
    ├── phase\_2\_build.diff       \# Generated code diff  
    ├── gate\_results.log         \# Test and linter execution output  
    └── phase\_3\_review.md        \# Documentation summary

#### **Root State Schema (session.json)**

{  
  "$schema": "http://json-schema.org/draft-07/schema\#",  
  "title": "AOPSessionState",  
  "type": "object",  
  "properties": {  
    "session\_id": { "type": "string" },  
    "goal": { "type": "string" },  
    "current\_phase": { "type": "string", "enum": \["scout", "build", "gate", "review", "completed", "failed"\] },  
    "retry\_count": { "type": "integer", "minimum": 0 },  
    "max\_retries": { "type": "integer", "default": 3 },  
    "artifacts": {  
      "type": "object",  
      "properties": {  
        "plan\_file": { "type": "string" },  
        "diff\_file": { "type": "string" },  
        "log\_file": { "type": "string" },  
        "review\_file": { "type": "string" }  
      }  
    },  
    "token\_metrics": {  
      "type": "object",  
      "properties": {  
        "prompt\_tokens": { "type": "integer" },  
        "completion\_tokens": { "type": "integer" },  
        "total\_cost\_usd": { "type": "number" }  
      }  
    }  
  },  
  "required": \["session\_id", "goal", "current\_phase", "retry\_count"\]  
}

### **3.3 Deterministic Gate Engine (adw/modules/runner.py)**

The gate engine executes local tools via subprocess calls without making external API calls.

#### **Verification Stages**

1. **AST Syntax Parse:** Evaluates modified Python files using ast.parse(). Fails immediately if syntax errors exist.  
2. **Linting Check:** Executes ruff check . \--quiet. Fails on unresolvable syntax/style violations.  
3. **Test Suite Execution:** Executes pytest \--maxfail=1 \--disable-warnings.

#### **Gate Output Contract**

class GateResult:  
    passed: bool  
    stage: str  \# "ast", "linter", or "pytest"  
    return\_code: int  
    stdout: str  
    stderr: str  
    error\_payload: Optional\[str\]  \# Truncated error message passed to context on failure

## **4\. Phase Execution Protocols**

### **Phase 1: Scout & Plan (Orchestrator)**

* **Model ID:** anthropic/claude-opus-5  
* **Temperature:** ![][image1]  
* **Input:** Raw user request/feature goal.  
* **Output Standard:** JSON object adhering to the Scout Plan Schema.

#### **Scout Plan Output Schema**

{  
  "summary": "Brief explanation of the engineering plan.",  
  "target\_files": \[  
    {  
      "path": "relative/file/path.py",  
      "action": "modify | create | delete"  
    }  
  \],  
  "steps": \[  
    "Step 1: Implement feature model",  
    "Step 2: Add validation logic",  
    "Step 3: Update unit tests"  
  \],  
  "test\_criteria": \[  
    "Unit test for light theme token toggle",  
    "Unit test for persistence in local storage"  
  \]  
}

### **Phase 2: Code Build (Construction)**

* **Model ID:** google/gemini-3.6-flash  
* **Temperature:** ![][image2]  
* **Input:** Phase 1 Scout Plan JSON \+ Optional error payload from Phase 3 Gate failure.  
* **Output Standard:** Complete Python module or standard git patch/diff block.

#### **Self-Healing Context Construction**

When Phase 2 is executed in a retry loop following a Gate failure, the user prompt is augmented as follows:

\[ SYSTEM PROMPT: You are a staff software engineer... \]

\[ USER PROMPT \]  
Primary Plan:  
\<JSON content of phase\_1\_plan.json\>

ATTENTION: Previous attempt failed deterministic gate checks. Fix the code to address the errors below.

Error Stage: \<gate\_stage\>  
Error Summary:  
\<gate\_error\_payload\>

### **Phase 3: Review & Documentation (Auditor)**

* **Model ID:** moonshot/kimi-k3  
* **Temperature:** ![][image1]  
* **Input:** Original Plan JSON \+ Verified Source Code Diff \+ Gate Execution Log.  
* **Output Standard:** Clean Markdown document containing release notes, API modifications, and architectural updates.

## **5\. Configuration Specification (config/roster.yaml)**

version: "1.0"  
factory:  
  name: "Autonomous OpenRouter Pipeline"  
  session\_root: "./sessions"  
  max\_retries: 3

models:  
  scout:  
    openrouter\_id: "anthropic/claude-opus-5"  
    fallback\_id: "openai/gpt-5-turbo"  
    parameters:  
      temperature: 0.2  
      max\_tokens: 4096  
      response\_format: "json\_object"

  builder:  
    openrouter\_id: "google/gemini-3.6-flash"  
    fallback\_id: "deepseek/deepseek-coder"  
    parameters:  
      temperature: 0.1  
      max\_tokens: 8192

  reviewer:  
    openrouter\_id: "moonshot/kimi-k3"  
    fallback\_id: "meta-llama/llama-3.3-70b-instruct"  
    parameters:  
      temperature: 0.2  
      max\_tokens: 4096

gates:  
  ast\_check: true  
  linter:  
    command: "ruff check . \--quiet"  
    allow\_failure: false  
  test\_runner:  
    command: "pytest \--maxfail=1 \--quiet"  
    timeout\_seconds: 45  
    allow\_failure: false

## **6\. Non-Functional Requirements (NFRs)**

* **Cost Optimization:** Overall execution cost per standard feature request should remain lower than ![][image3] by routing code generation tasks to Gemini 3.6 Flash.  
* **Execution Speed:** Code build completion should average ![][image4] per iteration using high-throughput OpenRouter endpoints.  
* **Determinism & Safety:** Zero unhandled model exceptions. Model outputs that fail JSON schema validation must trigger an immediate local parsing retry before calling remote APIs.  
* **Token Efficiency:** The prompt builder must truncate error payloads to a maximum of ![][image5] to avoid consuming model context windows during repair loops.

## **7\. Verification & Testing Matrix**

| Test ID | Target Component | Test Scenario | Expected Outcome |
| :---- | :---- | :---- | :---- |
| **TC-01** | openrouter.py | API key missing or invalid. | Raises explicit ValueError without sending requests. |
| **TC-02** | runner.py | Python file contains a syntax error (SyntaxError). | Gate fails at AST stage; subprocess execution halts. |
| **TC-03** | sdlc\_pipeline.py | Build agent outputs broken code; pytest fails. | Pipeline initiates Retry 1; injects pytest output into prompt context. |
| **TC-04** | sdlc\_pipeline.py | Build agent fixes code on Retry 1; pytest passes. | Pipeline advances to Review Phase; writes phase\_3\_review.md. |

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABoAAAAaCAYAAACpSkzOAAABmElEQVR4Xu2Uvy8EQRiG168CnUiWu93bu+gpLkE0EgkNqlNSSIQSjUInGoVCJE4l0Sh04ioSiahEqVBIrhONQvwDeN6z48bgKGQTyb3Jk7155/3mm53dPc+r6z+qyTV+KdXVrs3lcr6IoqiUyWR2uRZhSLhZV9lstgu2qDsR1FxynXdzFRE8FYQKGnPthjsRBEGHm7dF3QYMmDE14/CCN2jnKkqkEUeGHz0LHYPxGd8IjmHGzrsi80Bm37Ia8O5Z69jyPI/QpHYgGDYbn+CFwN+24p/EfIHN9jpeGfZsTwvOmjuyfcZnMQe2/5No2qdNcwOjHyaSbLRQqxHzh7ZfQ42CmnNq1txJPaMp7UAoaHwVxBSt+Lcitxmz6s5VZG5VpFKpTuOzq+uYZTv/lfSBwqIwHuut25nkGsWmXscyiw5rTMM2xo8iDMMeeSwyIfBK+Xy+xdSy0bHo7YjnRNx0hd87JvMuwv2CRlf6QOEIpoXJmIXI3PJv0epVH/6TOREbckvVDo58328nNJJOpwN37k+VWKO66kpEr4SykNqSqKPYAAAAAElFTkSuQmCC>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABoAAAAaCAYAAACpSkzOAAABX0lEQVR4Xu2UvUrEQBSFF38KtRERovkPPoBFwMJGsFZQ1lILQbDWR7FYGwsLXbETFhtBEEtLCwvBTixsfAP1u5KJd8cdF0GCQg58zN57T+Zkkt1tNGr9Rw3ajR/KfX2WZZ6QJEknjuN91hbMC7bXVpqm47CC90Dg+kPbUwrjhYCxKTXrNDwKYRhO2H6tImSX9Vrg85HtKVVJEI+MWfIqYJwyfeo7gUexof0u4W0X9A5io2WGbwLlkOmrO9xTdqf6BrHZpjmR7lNfFhzrvkt/Kmj7uyDmp7rvkgrqfWO8o7Xk8x0NmD71VUFL2Z3qG8S3btYE+b4/afqc5LZgR/tdUkFte/ahyoJEDB8ENl2QmsBR6hchiqIZ6fGIlwR6nTzPh7t36Ao6sWelONWcQNCN/EDhDNYF42GDLQHPPf8WI6rfFD/rU8EznMOq8XyR53ljGBaDIAjt2a+qsqBatSrROyC8jGMfA2RJAAAAAElFTkSuQmCC>

[image3]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHQAAAAZCAYAAADg8AqjAAAFjklEQVR4Xu2Za4hVVRTHb9rDnkY1Tc1rn7kzNDg9aXoQWklo9tAsLSsjJGYqpvoQTRKMfpBQkNRSKirSSKI+FJLKII1ND6TCwopwMq0+NBg9ofoQDPRh6v+/e62Zfdece+71RtbE/sOPe87aa6+zz17n7Me5uVxUVFRU1P9F+Xy+qaWlpdHaoyagmpubz3PO9SVJso1JLZPYydaQoUlCmkrZK4qP9l4IriNo9zWksbHxBhQdIeTUrsD3Kq3f0NBwLoHtMdDb1NTUQXA8DXEuog/65WoC20JhAeLcSHgtnF+WK30fh19o2EyChu3gTYGHwCqCm1tkfC+AfQA8CZ4itIU+VvBZLBxEvK343QL2EXTIxerHOIcaH/GmO99m8ifO++F/Vy5IqPP3cxC8LyymHde+HMe7CRJ2CR7g03G8RvgRLJD6NwmbeA2wFtdYItzr/P3sx/GVZKx1/5LQiG0CO/R2cCufPHn6BujT3t5+NMH597j5NtrQeWcT2Ibq6uqOK446JvjcSeD3NfgUbMR5O1EfjV1NfJSdRqSzl9tyikl2khi14fh1JoSEvhSu+4aThAa2eXINvpFFQtk9sA8T9Ns5tvywKib0P5JQNCThsIGfKbYsVGtr60nWFgqN+ErgPFRIKI6XCq/RBw1eRHD+Q0r9X2TeShXK7yCof4stU2nsauKbhC6z5RQTmsi8pzbnh/3lJPSVsk5XIqGIcWloVzk/VQyg/DlblilUekDYzcr43YuLPUI6OjqOCly5sJiM8pWBbZxQ/rzwq/PzQZf1wXVWE5QN2jLYDoBV1q5yMocyoXj7HH5nWx+NXU18k9BeW05JQucTtcF3HfhD4HphBvtPwYtwTBhDE8r5NrSrULZW+L3cSzQqbCumolGbSWCehCBdBPZdoFsu/jLh0x/4jhP8Tybw/UA6ZUTr5mS1ieONwiemOssGcY1nrV2F2LcR+L3t/Nu6NJFhngsR+mjsauLX19efSqTtqW8o7J06jahNFkGFt0rqEj7UZHRoVlWQ0PsExlloy1PFjudekdgyShLTA57mcEysT5ZQ72HnO16f3MJwhN+XhNQOB5usXaXthfJqw3E/QXtX81xjVxMf245TiMuYQ2HvwvXmElNU2FKh/Frnty6c58lIYlas5RIK/wcJfZJgaM9UTOh4TeiEUnoDqLQBlftAT21t7fHE+lLwm2ltoVD/fkK/RBZFaPzdwof0we8TBH6fpdT/gp1h7SqUnUnQ5mMDm87b+3iusauJz2mION+RK2w5BXs3mEPUhmvOCH2otra2EwlifQ7f9WFZBQndIDkZwRxaY8tLCpW2C+z4vPPj9nsCV2aFDTVu8iwC2xoTokjOLzpIb5BQ3TK8Sx/pkG6cf2uqs/5PoNPaVSj7jiTB6g/nLwhDPNfY1cTPjb1l34TXCMWOxgPVSgLbztAnlPNva9GokJVQ2XZpPxZ2BhWJQ5eTBVBor6mpOYHAvhIMorG7nEz4fJtDX6tEVrBMfjK2bXlU6KEPhu46gvNhLkBo46JCFhbDurjBTc8S+nWlxxsk6Ijz9ZpOhle+mRqfccrFzxLbCn5jP4R23H897I+HNgq2n8H1JKVsr7VrQhOzbcH5FOe/aH0pnBGWV6Ksb6OFp+VQXvlg2c8tC1e6+51/QjnMFX1TRWOXOP+gcCvypjB64072nGBI24COmE5Q99XEfyp7xfmpoi+cJhi7XPxycv4T4AHEWUGc/4T5Tlp/OL96fpHgeA9YlsgOAsfrdAuI8meI80M/E/oRzrcSnL8FPoZtfSK7BXudShQTWkJugib0H1Pi58+brT0Uh0T4zGInpXVUho7EsHtFuU9jfyN+QfwgwOsInO9S/7lBO1r0mMMyrjmbnx3102NUVFRUVFRUVFRUVDX6C/D8fKFL9NiYAAAAAElFTkSuQmCC>

[image4]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHMAAAAZCAYAAAACLBHaAAAFCElEQVR4Xu2YaWhdRRTH00Vb19YlRrO8e7NA9Km4RK0r1S5aS13ABYsiKBVqShXBtaLGBdxorGiMKbWWJhVpWoWgReryyfrBNY36oVDET2olokgrikr8/d89EyfDe/XFSC2P+cOfe+d/zj1nZs7cufNeVVVERERERETE/oQpoVAE5fhUDNI0XZzL5ZYnSXK6GNr3G7S0tBwu0smFdHoV122hj4Btpoh9E1zD4F6kfZ0Y+lYaGO8yuJuxLhVD+77AJLG6uvrQ0OBDRRQpzt1cX4ODoY+AvtnYbtJU7neIPHv+GOcKBEUcisWsEPwvxWTLnMYEL4HrRSa6LfQpBXwfL1ZMBnAs+u9ifX39yU53OeBLvn8lgjFu3yfFbG1tPUwk4Z2wj6LMC33KQaliNjY2Xow+IqqwTqe90viF7+8D/1mww/gsvp3kOc/3oX0L+gbsPSL3p/l2AX0Bfq/a85eJzsYCrkZ/1Pg8fMrvJ/enoj0He+Vr/itgV0NDwwXOz4EF26IYFudh9ScpUkz6cDX6Y0blXV1TU3OIH6ts1NXVHaVkBF0ncj8r9BkPShUT7frEiqmcTiffEyL6t76/Q21t7cHYPqqyLV8aObp55krng72T9kCaHa4Kp0U43NbWdoDZVxt7NVGaTO5/E9Umx9Hc76QoZ4iWYz7aNyqoSPuIXPYZ0RheEFmgNfAs7n9uamqaIerZNCv8LsUQ1W+0BWi/KLdo/ToXrnHjUH/TbCue6bR/BKumTkyyVdPDAE4Kff4trJjbQ508N9lElCrmsO/vgO14bL9yvUMkPnPWlGMSE+LUi9j/YAyXM6aDHNUHK0ij7KLeFsXUpBNrtqg2fs9gf29s5sJkb9H8iGqr0Op/mu0UhUWvAti4LhKlYdtKzJf9WAL2nalXTF3Rvua6WLQX62y3CMtCLGYFFdMFJNCbuXEcbsqBiknsoVDXZNugR5qbm49xOu2njTt8fx/EfBD7HqNivIE8hTyXiNLw6eb6iE99y7jeZc+M5PP5A8PYArb3ibOxiN4LB0Vra/su9N8bw2TLP1c0vx+VfzSQIQmKqS0erT+xxQb/hCvC58qCfugT+F4CrE+9lToR2Jv5eajzJrVq0MYTnM79WpHcA76/A7bjiJlXIUT1Ee1jrh3EPEW0mAvDZwWevcHlLbUDJdnBZksR/W3ybBWtrUNMYWfxdpdJ0oJifgk7RwMZkqyYt4pq058zrQaFP1N4/lp8vodzwmfLBoGmE6DduIGgi0KfclGqmEKafdyH8LnG0z4UNem+rwO2C4nX72u0l6Df5/kMoHX5PnoOXmEHqB9Ectzj7OqnqC07yQ4i3+lNcSdJLZwk2wnmuMl122yJN3OeKIH2VUl2aBsFW/yRaMP43CZK4/oQ2jLfj3YfPMfXJoKptpr70r//atvr/6iJ/WkAX4dfwZ/gO8b7nR9x8yLaZ/Bm2EX8VaIfz4cV89Mk2476VcRcduoe/dmgUyW2V9A2uiIl3jZH+0QRbRCfHvNZLno+c70cT3L9AO1GZ0+yb+IncDfcJurTgc9b3O9JbaFKM/+V6o/16fY02wH11jm2o3VwfVd9Erl/AN9ul/O/QCxmBRXTQd+CS8Vc8AN9otA/THR+tr6joS0EftPt4FL4nZnu5TeYtj5/qywG+Sh/qAs6RYo6KZfyGQ8UQ3T90Zau7VakOVnfS+kao6hT+JgAERERERERERERERWLvwDpmBu6LiHRMAAAAABJRU5ErkJggg==>

[image5]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAHEAAAAZCAYAAAAG2cHnAAAFVElEQVR4Xu2YaYhWVRjH7+hUYxu0DJOzvOedhQYHKnQqEKNo/VRkWTm2fAqasD20cqyIMhsrx63JMFGSKAsJWs1G81PQQhvqCEYLaJlJfUoJqabf/73n5MPhnZk7WjTk/cOf933+53nOPfc+Z3nuTZIcOXLkyJEjx1AYGwsZMSYWPAbTdZ3hrqXYCs9/HYVC4bxisXirc26aGLePWjQ0NNSK3MCN3MCrDP6p2CcLiJ1B7E5+Xxf5vw720+8U69fY2HgWeh9c5tmLT7v1AWPQn6afV4If/+ckwyc9qa+vHwdbYj0LGNtlXOszrvWGGLePWvAA7xAZ/EwGvkMPL/bJAuJugF/DLzxX8VDOtD5tbW1Ho/8AJwStqanpdOxdLS0tJwaN8dwJ1wZbwOdFONtq5YDPNJ/wQwKx3XkS8yT+t2DgHx9qEomdAafHugWJuZb+98Y62m4bi70N3mZ9aO9E2261csBn3eEkkTHOP5KT2KFEsLIKos6XJCpIeEBP0H+/1QS0LbCH1XiMyP8BfK+LfFRsDNgVa0HbQs8DcDN8TKSfi43PRJFxLoZL+D+7vb39KDH4xEkspLXCHHy7RJ3p0qurq48311itPmtra08N/WA/QNtKeBX/p4r8XwrvVWzwE/zkngef8VyBf5X1yYzDTOJ0YjfrpkUNFr6JflrwwX4efmnjvP4pfi9QkNSJ2AO6eetDn1d4vdHqAcSdIdK+RQnSVu5Zo3a0C3Rtsa6u7hSvddPvBjHxE05J1LhF2S5N1Db0u0SucTJyJdon8CEReyy/s+BrfjhhwmznGjvglaL8dC30ucEPewr2Cv3XceOPnP6amprjgs+I4JO4MNazQKuPKrfZasV0Ri8ONn2vcYMkEb7EDTWJbogk6gy1egz8PiqW2U6J/Rw+KBpZydjvebUEn8S3PC/CXq0Ha2J0X6rED2jliaqIm5ubG9D2+ep4nPyw3xZtLPaTxL8X7GL6SvMtvx2aICL2ZJoqTVh25Ek8wpNI3PgweKNpf//G2D1wq/XxuqrZpTrvRJeeiddYn2J6pgz47WxQKIn4dVnNb1F/6oGJtg39RxF9gWyfRFXQ4lewL4nOdny70X+Fj8YM792+b23Ly8rEbgy2tk2XvlP/HsgY7rMxI4JPYk+sZwFx38OVVmMwz6HtCbafdbutj4C2E95ubFWrnZHPzfCnZJivNz6Jf682+rlHv2hbXXpuzTJtVdi/eZZ0n8R3RL8qlMy4Ur4b7Q9W4bGibbNwaU2wxGqFtLjbFGwSfk5ra+sJ6CcV0+KwQ/fJWX6+jcsMJZHOFsW6ZpY7uMVMituFYvq1Z6LVsD+kv+XGHq8HxmqrDpp/UPspNuqNX+krTbAFjQvtWauVAz7r4ePB1kTyelcxenVAuxD+LIaql/YF2C+LPl7b+D44QZTmd4tdLvo8h+/9KqRCMeXS7bTcSnzf2I9gz7Q+9LOWZ3621YaElq6om6OzX1w680rVGfrl8vEv5HvEeGYF0DZZiYSdIvYa4t+NXwnQbtJNwOtF7A34T7U+vnz/QA/F+dUD1w816wP8p7PvNE6RPi71TRWFgx82dDb3wo1aCaIcXHpmalco3at88L/FpWemkiaqGi2tIJd+PuzDZzlcxPU6wjjQV8G9Lt2h9PowD5+HXfp8NXFKE8UncVMh3bVK1S7sDf1kQp7E/0ESsyK8iHOx+XGbQaX2clHvbMkg55e2UPq5RAxbTxlUcDOTaD9XTDJ8/A7Qy/tQ55XOIFWTySDjGykYn0tGMD4Ls41XBcY+/xic3//dIC/bOUY/tCrminFDjhw5cuTIkSPHaMBfy080VSk8w6AAAAAASUVORK5CYII=>