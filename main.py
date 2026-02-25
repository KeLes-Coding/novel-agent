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
        choices=["ideation", "outline", "bible", "plan", "draft", "review", "export"],
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

    
    # 0. 导入 ProjectPhase (放在这里或文件头部)
    from core.fsm import ProjectPhase

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

    elif args.step:
        # 如果指定了 --step，优先处理状态切换
        step_mapping = {
            "ideation": ProjectPhase.IDEATION,
            "outline": ProjectPhase.OUTLINE,
            "bible": ProjectPhase.BIBLE,
            "plan": ProjectPhase.SCENE_PLAN,
            "draft": ProjectPhase.DRAFTING,
            "review": ProjectPhase.REVIEW,
            "export": ProjectPhase.EXPORT
        }
        target_phase = step_mapping.get(args.step)
        
        # 仅当处于 auto 模式时，才强制切换状态以设定起点
        # 如果是单步执行(非 auto)，原来的逻辑(调用 manager.run_xxx)会处理流转
        if args.auto and target_phase:
             manager.fsm.transition_to(target_phase, force=True)

        if args.auto:
             # 如果是 Step + Auto，切换完状态后进入 Auto 逻辑
             pass 
        else:
            # 单步执行逻辑
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
            elif args.step == "review":
                manager.run_review()
            elif args.step == "export":
                manager.run_export()
            # 执行完单步退出
            return

    if args.auto:
        cli.notify("模式", "启动自动推进模式 (按 Ctrl+C 终止)...")
        try:
            # 循环直到项目完成
            while manager.state.step != "done":
                manager.execute_next_step()
                
            cli.notify("结束", "自动模式执行完毕。")
            
        except KeyboardInterrupt:
            cli.notify("终止", "用户手动停止自动模式。")
        except Exception as e:
            import traceback
            traceback.print_exc()
            cli.notify("执行中断", str(e))

    else:
        if not args.rollback and not args.step:
            print("请指定 --step <步骤名> 或 --auto 或 --rollback")

if __name__ == "__main__":
    main()
