# main.py
import argparse
import sys
import os

# 获取当前脚本所在目录
current_dir = os.path.dirname(os.path.abspath(__file__))
# 拼接 src 目录路径
src_path = os.path.join(current_dir, "src")
# 加入系统路径
sys.path.append(src_path)

from core.manager import ProjectManager


def main():
    parser = argparse.ArgumentParser(description="Novel Agent CLI")
    parser.add_argument(
        "--config", default="config/config.yaml", help="Path to config file"
    )
    parser.add_argument("--run-id", help="Resume an existing run ID")
    parser.add_argument(
        "--step",
        choices=["ideation", "outline", "bible", "plan", "draft"],
        help="Execute a specific step",
    )
    parser.add_argument(
        "--auto", action="store_true", help="Run full pipeline automatically"
    )

    args = parser.parse_args()

    # 初始化管理器
    try:
        manager = ProjectManager(config_path=args.config, run_id=args.run_id)
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"Error initializing project: {e}")
        sys.exit(1)

    print(f"🚀 Project: {manager.run_id} | Dir: {manager.run_dir}")

    if args.auto:
        print("⚡ Auto mode initiated...")

        # 1. 创意阶段
        if not manager.state.idea_path:
            print(">> Running Ideation...")
            manager.run_ideation()
        else:
            print(f"✓ Ideation done: {manager.state.idea_path}")

        # 2. 大纲阶段
        if not manager.state.outline_path:
            print(">> Running Outline...")
            manager.run_outline()
        else:
            print(f"✓ Outline done: {manager.state.outline_path}")

        # 3. 设定集阶段
        if not manager.state.bible_path:
            print(">> Running Bible...")
            manager.run_bible()
        else:
            print(f"✓ Bible done: {manager.state.bible_path}")

        # 4. 分场阶段
        if not manager.state.scenes:
            print(">> Initializing Scenes...")
            manager.init_scenes()
        else:
            print(f"✓ Scenes initialized: {len(manager.state.scenes)} scenes")

        # 5. 正文阶段
        print(">> Running Drafting Loop...")
        manager.run_drafting_loop()

    elif args.step:
        # 手动单步模式
        if args.step == "ideation":
            manager.run_ideation()
        elif args.step == "outline":
            manager.run_outline()
        elif args.step == "bible":
            manager.run_bible()
        elif args.step == "plan":
            manager.init_scenes()
        elif args.step == "draft":
            manager.run_drafting_loop()

    else:
        print("Please specify --step or --auto")


if __name__ == "__main__":
    main()
