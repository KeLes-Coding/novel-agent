# API Manual: Novel Agent CLI & Backend

Novel Agent provides a command-line interface (CLI) for story generation management.

## CLI Usage

Run the program using `python main.py [args]`.

### Arguments

-   `--config <path>`: Specifies the configuration file path (default: `config/config.yaml`).
-   `--run-id <ID>`: Resumes a specific project run.
-   `--step <step_name>`: Executes a specific pipeline step.
    -   `ideation`: Generate initial story concepts.
    -   `outline`: Generate the story outline.
    -   `bible`: Create the world/character bible.
    -   `plan`: Generate scene plans.
    -   `draft`: Generate the actual prose.
-   `--rollback <step_name>`: Rolls back to a previous state (Warning: This will reset progress).
-   `--auto`: Runs the pipeline continuously in auto-mode until complete or interrupted.

### Examples

**New Project (Auto Mode):**
```bash
python main.py --auto
```

**Resume Project (Specific Step):**
```bash
python main.py --run-id "2023-10-27_abc123" --step outline
```

## Backend API (Planned)

The following endpoints are planned for future integration with a web frontend.

### Project Management

-   `POST /api/project/init`: Initialize a new project run.
-   `GET /api/project/status`: Get the current status (phase, last updated).
-   `GET /api/projects`: List all project runs.

### Artifacts (Read Only)

-   `GET /api/artifacts/{run_id}/ideation`: Get ideation artifacts.
-   `GET /api/artifacts/{run_id}/outline`: Get outline artifacts.
-   `GET /api/artifacts/{run_id}/bible`: Get bible artifacts.
-   `GET /api/artifacts/{run_id}/scenes/{scene_id}`: Get generated scene content.

### Actions (Write)

-   `POST /api/step/run`: Trigger execution of the current step.
-   `POST /api/step/rollback`: Rollback to a previous step.
-   `POST /api/config/update`: Update prompts or model configurations.
