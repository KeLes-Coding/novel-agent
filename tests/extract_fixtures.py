import json
import os
import sys

# 设置 BASE_DIR 为项目根目录 (tests/.. -> root)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 指定要抓取的 Run 路径 (相对于根目录)
RUN_REL_PATH = os.path.join("runs", "2026-01-15", "16-55-35_b6901ee8")
RUN_DIR = os.path.join(BASE_DIR, RUN_REL_PATH)

# 测试数据存储路径 (tests/data/fixtures.json)
TEST_DATA_DIR = os.path.join(BASE_DIR, "tests", "data")
FIXTURES_FILE = os.path.join(TEST_DATA_DIR, "fixtures.json")

def normalize_path(path):
    # 处理路径分隔符并转换为绝对路径
    # 如果路径已经是绝对路径且存在，则直接用；否则视为相对于 BASE_DIR
    if os.path.isabs(path):
        return path
    return os.path.join(BASE_DIR, path.replace("\\", os.sep).replace("/", os.sep))

def read_text(path):
    full_path = normalize_path(path)
    if not os.path.exists(full_path):
        print(f"Warning: File not found: {full_path}")
        return None
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()

def read_json(path):
    content = read_text(path)
    if content:
        return json.loads(content)
    return None

def main():
    print(f"Reading state from {RUN_DIR}")
    state_path = os.path.join(RUN_DIR, "state.json")
    if not os.path.exists(state_path):
        print(f"Error: state.json not found at {state_path}")
        return

    with open(state_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    fixtures = {
        "meta": {
            "source_run": RUN_REL_PATH,
            "description": "Fixed test data extracted from actual run"
        }
    }

    # 1. Ideation
    print("Extracting Ideation...")
    if "idea_path" in state:
        fixtures["ideation"] = read_text(state["idea_path"])

    # 2. Outline
    print("Extracting Outline...")
    if "outline_path" in state:
        fixtures["outline"] = read_text(state["outline_path"])

    # 3. Bible
    print("Extracting Bible...")
    if "bible_path" in state:
        fixtures["bible"] = read_text(state["bible_path"])

    # 4. Scene Plan
    print("Extracting Scene Plan...")
    if "scene_plan_path" in state:
        fixtures["scene_plan"] = read_json(state["scene_plan_path"])

    # 5. Scenes
    print("Extracting Scenes...")
    if "scenes" in state:
        fixtures["scenes"] = []
        for scene in state["scenes"]:
            scene_data = scene.copy()
            if "content_path" in scene:
                scene_data["content"] = read_text(scene["content_path"])
            fixtures["scenes"].append(scene_data)

    # Save
    os.makedirs(TEST_DATA_DIR, exist_ok=True)
    with open(FIXTURES_FILE, "w", encoding="utf-8") as f:
        json.dump(fixtures, f, ensure_ascii=False, indent=2)
    
    print(f"Successfully created fixtures at {FIXTURES_FILE}")

if __name__ == "__main__":
    main()
