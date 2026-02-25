# API Manual: Novel Agent CLI & Backend

Novel Agent provides a command-line interface (CLI) for story generation management.

## CLI Usage

Run the program using `python main.py [args]`.

### Core Pipeline Arguments

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

---

## Phase 4: Style & Polishing Tools

Phase 4 introduces tools for corpus ingestion, style learning, and automatic text polishing.

### 1. Corpus Ingestion (Pre-processing)

Use these tools to prepare your novel corpus for style learning.
**Location**: `src/tools/ingest/`

### Automated Pipeline (Recommended)

The `ingest_pipeline.py` script automates normalization, splitting, and indexing. It supports both single files and directory inputs.

**Usage:**
```bash
python src/tools/ingest_pipeline.py --input "data/corpus/" --output_dir "data/corpus/processed/" --author "AuthorName"
```
- `--input`: Path to a single file (`.txt`, `.docx`, `.epub`) or a directory containing novel files.
- `--output_dir`: Base directory for output. Creates `clean/` and `processed/` subdirectories.

### Manual Steps (Advanced)

If you need granular control, you can run each step individually:

**Step 1: Normalize Text**
Cleans raw text, removes ads, and fixes broken lines.
Supports: `.txt`, `.docx`, `.epub`, `.json`.
```bash
python src/tools/ingest/normalize.py --input "data/corpus/raw/my_novel.docx" --output "data/corpus/clean/my_novel_clean.txt"
```

**Step 2: Split & Tag**
Splits cleaned text into chunks (Generic & Elite) for the Style Bank.
```bash
python src/tools/ingest/splitter.py --input "data/corpus/clean/my_novel_clean.txt" --output_dir "data/corpus/processed/" --author "AuthorName"
```
*Output*: Generates `style_chunks.jsonl` and `style_elite.jsonl` in the output directory.

### 2. Style Bank (Vector DB)

Manages the vector database for style retrieval.
**Location**: `src/style/`

**Indexing (Import Data)**
Imports JSONL chunks into the local ChromaDB.
```bash
python src/style/indexer.py --input "data/corpus/processed/style_elite.jsonl"
```

**Retrieval (Test)**
Test semantic search manually.
```bash
python src/style/retriever.py --query "Combat scene with fire magic" --n 5
```

### 3. Auto-Polishing (Configuration)

To enable possible "Writer -> Reader -> Polisher" loop during drafting:

1.  Open `config/config.yaml`.
2.  Set `workflow.auto_polish` to `true`.

```yaml
workflow:
  branching:
    enabled: true
  auto_polish: true  # <--- Enable this
```

When enabled, the `WorkflowEngine` will automatically:
1.  Generate a draft (`Writer`).
2.  Analyze it (`Reader`).
3.  Refine it (`Polisher`) if necessary.
