# Implementation Plan - CLI Loop & Project Documentation

## Goal
1.  Fix `main.py` so that `--auto` mode continuously executes subsequent stages until completion or user interruption.
2.  Create comprehensive documentation as requested by the user.

## Proposed Changes

### Logic

#### [MODIFY] [main.py](file:///i:/WorkSpace/novel-agent/main.py)
- Change the `args.auto` block to a loop:
  ```python
  elif args.auto:
      cli.notify("模式", "启动自动推进模式...")
      while manager.state.step != "done": # Check termination condition
          try:
              manager.execute_next_step()
              # Optional: Add a small pause or check if we should continue? 
              # Since each step has HITL, the user is already "pausing" there.
          except Exception as e:
              # Log and break
              pass
  ```
- Add a graceful exit mechanism if needed (Ctrl+C is standard).

### Documentation

#### [NEW] [doc/Project_Overview.md](file:///i:/WorkSpace/novel-agent/doc/Project_Overview.md)
- **Project Structure**: Explain `src/core`, `src/agents`, `src/pipeline`.
- **Tech Stack**: Python, Jinja2, OpenAI SDK (Generic), Pytest.
- **Memory Mechanism**: Explain the hierarchical memory (Scene -> Chapter Summary -> Bible) and Dynamic Bible Update.

#### [NEW] [doc/API_Manual.md](file:///i:/WorkSpace/novel-agent/doc/API_Manual.md)
- **Backend Interface (Planned)**:
    - `POST /api/project/init`
    - `GET /api/project/status`
    - `POST /api/step/run`
    - `GET /api/artifacts/{path}`
- **CLI Usage**:
    - `python main.py --auto`
    - `python main.py --step ideation`

## Verification Plan

### Automated Tests
- None for CLI loop (interactive).
- Documentation review.

### Manual Verification
1.  Run `python main.py --auto` and verify it proceeds from `ideation` -> `outline` automatically after user selection.
