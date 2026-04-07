# Technical Design Document: AIgentOS

**Status:** Initial Architecture / Baseline Release  
**Version:** 0.1.0-oss  
**Repository:** [ifinspire/AIgentOS](https://github.com/ifinspire/AIgentOS)

---

## 1. Overview
**AIgentOS** is a kernel-first, open-source environment for self-hosted AI chatbots. Unlike traditional chatbot wrappers, AIgentOS prioritizes the **Kernel**—the logic layer that handles orchestration, prompt construction, and state management—as the primary product. It is designed to be lightweight, transparent, and containerized, providing a stable foundation for multi-turn AI interactions using local LLMs.

The project ships with a reference WebUI, but the core value is in the kernel's ability to manage "Agent Profiles" and provide deep visibility into runtime performance and prompt logic.

## 2. Motivation
The question we asked was: "What if we can make highly personalized self-hosting AI chatbots as simple as website templates?"
The self-hosted AI chatbot landscape currently ranges between high-level, feature-heavy platforms (e.g., Open WebUI) and low-level script-based implementations. AIgentOS was designed with the following tenets:
*   **Reusablity:** Developing multiple AI agents often results in redundant "plumbing." AIgentOS abstracts the redundant plumbings into a reusable kernel.
*   **Transparency:** It is often difficult to understand how existing frameworks construct prompts for debugging and performance tracking. AIgentOS provides explicit debug surfaces and performance data to show exactly how prompts are constructed and how long each turn takes.
*   **Predictability:** AIgentOS prioritizes consistency and privacy by focusing on a "baseline" performance for smaller self-hosted models such as the [SmolLM model family](https://huggingface.co/collections/HuggingFaceTB/smollm).

## 3. Project Components & Requirements

### 3.1 Kernel (Core Orchestrator)
*   **Framework:** FastAPI (Python).
*   **Purpose:** Manages the lifecycle of a chat session, handles API requests, and interfaces with the LLM provider (Ollama).
*   **Endpoints:** Includes `/api/chat` for interactions, `/api/prompts` for configuration, and `/api/performance` for telemetry.

### 3.2 Agent Prompt Management
*   **Location:** `/agent-prompts`.
*   **Purpose:** A component-based system for building system prompts. It allows for "Profiles" (personas) and "Components" (reusable instructions like "Be concise" or "Use Markdown").

### 3.3 Memory & Persistence
*   **Storage:** SQLite (`/models-local/chat.db`).
*   **Requirement:** Must support multi-turn conversation history and metadata (latency, token usage) without requiring a heavy database like PostgreSQL or a Vector DB in the initial baseline.

### 3.4 WebUI (Optional Interface)
*   **Framework:** Vite + React (TypeScript).
*   **Purpose:** A lightweight "reference client" to interact with the kernel. This example webui is optional and decoupled from the kernel.

### 3.5 Infrastructure
*   **Deployment:** Docker + Docker Compose.
*   **Inference:** For this version, [Ollama](https://ollama.com/) (running on the host or in a sidecar) is a strict requirement for the baseline configuration.

## 4. Out of Scope
To maintain the "lightweight" and "kernel-first" philosophy, the following are intentionally excluded from the current design:
*   **SaaS/Multi-tenancy:** AIgentOS is designed for personal, self-hosted use. There is no built-in user authentication or billing system.
*   **Cloud LLM Dependencies:** While adapters could be written, the current design prioritizes local-first inference using Ollama.
*   **Complex Multi-Agent Swarms:** This version focuses on single-agent, multi-turn chat. Orchestrating "swarms" of 10+ agents is currently out of scope.
*   **Plugin Store:** Extensions are handled through direct code/profile additions rather than a runtime marketplace.

## 5. Practical Technical Decisions

### 5.1 Technology Stack & Rationale
| Decision | Choice | Rationale |
| :--- | :--- | :--- |
| **Backend** | Python / FastAPI | Standard support for async LLM calls and widespread AI ecosystem libraries. |
| **Frontend** | React / Vite | Rapid development and high component reusability for the reference UI. |
| **Database** | SQLite | Zero-configuration persistence that follows the "file-based" portability of the project. |
| **Licensing** | Split (MPL & Apache) | Protects the core Kernel (MPL-2.0) while allowing users to freely modify UI and Prompts (Apache-2.0). |

### 5.2 Tradeoff Decisions
1.  **Monorepo vs. Polyrepo:**
    *   *Decision:* Single repository for initial OSS release.
    *   *Reason:* Easier onboarding and version synchronization at the cost of slightly larger download sizes for users who only want the kernel.
2.  **SmolLM3 as Default Target:**
    *   *Decision:* SmolLM3 by default because it fits well in most consumer devices while having comparable chat/inference performance to larger models.
    *   *Reason:* While it works with larger models (Llama 3, etc.), the "baseline" benchmarks are tuned for small, fast, local models to ensure it runs on consumer hardware (e.g., MacBook Air/Pro). Further, SmolLM3 is a fully open model - meaning users can review the training data for biases, transparency, and ethical concerns.
3.  **No Vector DB by Default:**
    *   *Decision:* Use simple SQLite history.
    *   *Reason:* Limits "infinite" long-term memory retrieval but significantly reduces the architectural complexity and resource footprint for the average user.

## 6. Architecture Diagram (Simplified)
```text
[ User / WebUI ] <---> [ FastAPI Kernel ] <---> [ SQLite ]
                              |
                              +---> [ Agent Prompts (JSON/MD) ]
                              |
                              +---> [ Ollama (Local LLM) ]
```
