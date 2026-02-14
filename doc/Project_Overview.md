# Project Overview: Novel Agent

Novel Agent is an AI-powered long-form novel generation system. It utilizes a multi-stage pipeline (Ideation -> Outline -> Bible -> Scene Planning -> Drafting) to create coherent and engaging stories.

## Architecture

The system is built upon a modular architecture:

-   **`src/core`**: The kernel of the application.
    -   `manager.py`: The `ProjectManager` class orchestrates the entire lifecycle, managing state transitions and workflow execution.
    -   `state.py`: Defines data structures (`ProjectState`, `SceneNode`) to persist progress.
    -   `context.py`: Responsible for assembling the prompt context for the LLM, including relevant bible entries and memory.
    -   `fsm.py`: A Finite State Machine that governs the project phases.

-   **`src/agents`**: Specialized agents for specific tasks.
    -   `wiki_updater.py`: Handles memory consolidation and dynamic bible updates.

-   **`src/pipeline`**: Contains the logic for each generation phase.
    -   `step_01_ideation.py`: Generates initial concepts.
    -   `step_02_outline.py`: Structures the story.
    -   `step_03_bible.py`: Defines the world and characters.
    -   `step_04_scene_plan.py`: Breaks down the outline into scenes.
    -   `step_04_drafting.py`: Generates the actual prose.

-   **`src/providers`**: Abstraction layer for LLM providers (OpenAI compatible).

## Technology Stack

-   **Language**: Python 3.10+
-   **LLM Integration**: `openai` SDK (supports OpenAI, DeepSeek, etc.)
-   **Templating**: `jinja2` for prompt engineering.
-   **Testing**: `pytest` for unit and integration tests.
-   **Data Storage**: File-based storage (Markdown for artifacts, JSON for state).

## Memory Mechanisms

To solve the "Context Amnesia" problem in long texts, Novel Agent implements a two-tier memory system:

### 1. Hierarchical Memory (Story Context)
-   **Short-term**: The system keeps the summaries of the most recent 5 scenes active in the context.
-   **Long-term (Archived)**: Every 10 scenes, the system triggers a "Memory Consolidation" process. It aggregates the summaries of old scenes into a high-level "Chapter Summary" (stored in `ProjectState.archived_summaries`) to reduce token usage while retaining the main plot arc.

### 2. Dynamic World Building (World Context)
-   **Problem**: Static "Bibles" (character sheets) become outdated as the story progresses (e.g., a character dies, or a new location is discovered).
-   **Solution**:
    -   **Piggyback Extraction**: When generating the summary for a newly written scene, the **WikiUpdater** also extracts "New Facts" (new characters, status changes).
    -   **Append-Only Update**: These facts are safely appended to the `bible_selected.md` file under a `## [New] Dynamic Updates` section. This allows the world setting to evolve with the story automatically.
