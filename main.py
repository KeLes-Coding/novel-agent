# main.py
import argparse
import sys
import os

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(current_dir, "src")
sys.path.append(src_path)

from core.manager import ProjectManager
from interfaces.cli import CLIInterface

def main():
    parser = argparse.ArgumentParser(description="Novel Agent CLI 中文版 (v2.1)")
    parser.add_argument(
        "--config", default="config/config.yaml", help="配置文件路径"
    )
    parser.add_argument("--run-id", help="恢复已有的运行 ID")
    parser.add_argument(
        "--step",
        choices=["ideation", "outline", "bible", "plan", "draft"],
        help="执行特定步骤 (这将强制跳转状态)",
    )
    parser.add_argument(
        "--rollback",
        choices=["ideation", "outline", "bible", "plan"],
        help="回退到指定阶段 (Backtracking)",
    )
    parser.add_argument(
        "--auto", action="store_true", help="自动执行 (基于当前状态推进)"
    )

    args = parser.parse_args()

    # 初始化界面
    cli = CLIInterface()

    # 初始化管理器
    try:
        manager = ProjectManager(config_path=args.config, interface=cli, run_id=args.run_id)
    except Exception as e:
        import traceback
        traceback.print_exc()
        cli.notify("致命错误", f"项目初始化失败: {e}")
        sys.exit(1)

    cli.notify("项目状态", f"项目 ID: {manager.run_id}\n当前阶段: {manager.fsm.current_phase.value}", {"存储目录": manager.run_dir})

    if args.rollback:
        # 映射别名到 Enum 值
        mapping = {
            "ideation": "ideation",
            "outline": "outline",
            "bible": "bible",
            "plan": "scene_plan"
        }
        target = mapping.get(args.rollback, args.rollback)
        if cli.confirm(f"警告：你确定要回退到 [{target}] 阶段吗？这只是重置状态，不会删除文件，但后续生成可能会覆盖现有内容。"):
            manager.rollback(target)
        else:
            cli.notify("取消", "回退操作已取消。")

    elif args.auto:
        cli.notify("模式", "启动自动推进模式...")
        try:
            manager.execute_next_step()
        except Exception as e:
            cli.notify("执行中断", str(e))

    elif args.step:
        if args.step == "ideation":
            manager.run_ideation()
        elif args.step == "outline":
            manager.run_outline()
        elif args.step == "bible":
            manager.run_bible()
        elif args.step == "plan":
            manager.init_scenes()
        elif args.step == "draft":
            manager.run_drafting_loop(auto_mode=args.auto)

    else:
        if not args.rollback:
            print("请指定 --step <步骤名> 或 --auto 或 --rollback")

if __name__ == "__main__":
    main()
