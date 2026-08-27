# Architecture Overview: Project Captn

## Vision
**Project Captn** is a **Deterministic Agent Runtime** designed to move beyond simple AI prompts toward a robust execution ecosystem. The core philosophy is the separation of "The Brain" (Orchestration & Logic) from "The Hands" (Capabilities & Tools).

## Core Principles

### 1. The Brain and Hands Model
- **The Brain (Orchestrator/Captn)**: Handles high-level planning, state management, routing, and reasoning. It decides *what* needs to be done and *who* should do it.
- **The Hands (Workers)**: Specialized agents or scripts that execute specific tasks. They are "dumb" in the sense that they follow instructions from the Brain but are "smart" in their specific domain (e.g., mathematical synthesis, code generation, web searching).
- **The Bloodstream (Queues/Messages)**: Communication between the Brain and the Hands is strictly structured (JSON). This ensures predictability and allows for easier debugging and auditing.

### 2. Modular Plugin System
The framework is designed to be extensible. Capabilities are not hard-coded into the core runtime but are provided via **Plugins**:
- **Logic Plugins**: Define new orchestrator behaviors (e.g., "Debate," "Evolution," "Repair").
- **Tool Plugins**: Provide specific capabilities (e.g., "File System Access," "Web Search," "Image Generation").

### 3. Deterministic Runtime
To ensure reliability, Captn focuses on:
- **Session Isolation**: Each "Project" or "Session" has its own isolated workspace, memory, and state.
- **Rollback Mechanism**: Every significant change made by a Worker can be rolled back if it fails a validation check.
- **State Persistence**: The system maintains a persistent `shared_research_memory.json` that acts as the "Long-Term Memory" of the project.

### 4. The "Tinker" and the "Engine"
- **The Tinker (Human)**: Provides the vision, the soul, and the creative direction. Defines the goals and the architectural boundaries.
- **The Engine (AI)**: Handles the logic, code generation, and execution of the plan. It is the machinery that powers the Tinker's vision.

---
**Architect: Célestin Martin**
*Designed for the next generation of autonomous agentic workflows.*
