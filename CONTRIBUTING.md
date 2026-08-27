# Contributing to Captn

Thank you for your interest in contributing to Captn! We welcome contributions from the community to help evolve this experimental runtime.

## How to Contribute

### 1. Explore the Project
Read the `docs/architecture` and `docs/prior-art` to understand our core philosophy of separating cognition from execution.

### 2. Development Workflow
- **Feature Branch:** Create a new branch for your feature/fix.
- **Pull Requests:** Submit a PR with a clear description of the changes.
- **Testing:** Ensure your changes don't break the core `MessageBus` or `StateStore`.

### 3. Guidelines
- **Modularity:** Keep worker logic isolated. New workers should be added as plugins.
- **JSON First:** All inter-component communication must remain strictly JSON.
- **Deterministic Focus:** Ensure that for a given state and input, the runtime produces a predictable output.

## Code of Conduct
We are committed to providing a positive experience for every developer. Please be respectful and constructive in all interactions.
