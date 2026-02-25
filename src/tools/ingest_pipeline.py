import logging
import sys
import os
import argparse
import json
from pathlib import Path

# 添加 src 到 sys.path 以便导入模块
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))

from src.tools.ingest.normalize import process_file as normalize_process_file
from src.tools.ingest.splitter import get_chunks as splitter_get_chunks
# from src.style.indexer import main as indexer_main (Moved to lazy import)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_pipeline(input_path_str: str, output_base: str, author: str = "Unknown"):
    """
    执行完整的语料处理流程: 清洗 -> 切分 -> 入库
    支持单文件或文件夹批量处理
    """
    input_path = Path(input_path_str)
    if not input_path.exists():
        logger.error(f"输入路径不存在: {input_path_str}")
        return

    # 1. 准备目录
    clean_dir = Path(output_base) / "clean"
    processed_dir = Path(output_base) / "processed"
    clean_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    # 准备输出文件 (追加模式需要注意清理旧文件? 这里假设每次运行是新的或追加)
    # 为了简单起见，如果文件存在，我们先清空，或者每次运行生成新的?
    # 鉴于 pipeline 性质，追加可能是危险的如果重复运行。
    # 建议：如果是目录模式，先清空目标 jsonl? 或者由用户管理。
    # 这里我们采取“覆盖”策略：对于本轮运行产生的 jsonl，我们重新创建。
    
    style_chunks_path = processed_dir / "style_chunks.jsonl"
    style_elite_path = processed_dir / "style_elite.jsonl"
    
    # 初始化/清空输出文件
    with open(style_chunks_path, 'w', encoding='utf-8') as f:
        pass
    with open(style_elite_path, 'w', encoding='utf-8') as f:
        pass

    logger.info(f"Scanning input path: {input_path.absolute()}")
    
    # 2. 收集文件
    files_to_process = []
    if input_path.is_file():
        files_to_process.append(input_path)
    else:
        # Recursive scan
        supported_exts = {'.txt', '.docx', '.epub', '.json'}
        for root, _, filenames in os.walk(input_path):
            for name in filenames:
                if os.path.splitext(name)[1].lower() in supported_exts:
                    files_to_process.append(Path(root) / name)
    
    logger.info(f"Found {len(files_to_process)} files to process.")
    
    # 3. 逐个处理: 清洗 -> 切分 -> 追加写入
    total_generic = 0
    total_elite = 0
    
    for idx, raw_file in enumerate(files_to_process):
        logger.info(f"[{idx+1}/{len(files_to_process)}] 处理: {raw_file.name}")
        
        # Step A: Normalize
        # 使用相对路径保持结构在 clean_dir 中? 或者全部平铺?
        # 为了简单，平铺，文件名加 hash 防止冲突? 或者直接用文件名(假设无重复)
        # 这里直接用文件名，重名会覆盖(简单处理)
        clean_filename = f"{raw_file.stem}_clean.txt"
        clean_output_path = clean_dir / clean_filename
        
        try:
            normalize_process_file(str(raw_file), str(clean_output_path))
        except Exception as e:
            logger.error(f"清洗失败 {raw_file}: {e}")
            continue
            
        # Step B: Read & Split
        try:
            with open(clean_output_path, 'r', encoding='utf-8') as f:
                cleaned_text = f.read()
                
            # 使用 book 名作为文件名(不含扩展)
            book_name = raw_file.stem
            chunks = splitter_get_chunks(cleaned_text, author=author, book=book_name)
            
            # Step C: Append Write
            with open(style_chunks_path, 'a', encoding='utf-8') as f:
                for r in chunks["generic"]:
                    f.write(json.dumps(r, ensure_ascii=False) + '\n')
                    
            with open(style_elite_path, 'a', encoding='utf-8') as f:
                for r in chunks["elite"]:
                    f.write(json.dumps(r, ensure_ascii=False) + '\n')
            
            total_generic += len(chunks["generic"])
            total_elite += len(chunks["elite"])
            
        except Exception as e:
            logger.error(f"切分失败 {clean_output_path}: {e}")
            continue

    logger.info(f"=== 处理完成 ===")
    logger.info(f"总计生成 chunks: {total_generic}, 精选 elite: {total_elite}")

    # 4. 入库 (Indexer)
    logger.info("=== 步骤 3: 构建索引 (Indexer) ===")
    
    if total_elite == 0:
        logger.warning("没有生成精选切片，跳过索引构建。")
        return

    # 调用 Indexer
    try:
        from src.style.indexer import StyleIndexer
        indexer = StyleIndexer(db_path=os.path.join(output_base, "chroma_db"))
        indexer.index_file(str(style_elite_path))
    except ImportError:
        logger.error("未找到 'chromadb' 模块或 'src.style.indexer'，跳过索引步骤。请安装 chromadb: pip install chromadb")
    except Exception as e:
        logger.error(f"构建索引失败: {e}")
        return

    logger.info("=== 语料工程流程全部完成 ===")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="一键式语料工程工具: 清洗 -> 切分 -> 入库")
    parser.add_argument("--input", required=True, help="原始语料文件路径或文件夹 (.txt, .docx, .epub)")
    parser.add_argument("--output_dir", default="data/corpus", help="输出基准目录")
    parser.add_argument("--author", default="Unknown", help="作者名 (用于元数据)")

    args = parser.parse_args()
    
    run_pipeline(args.input, args.output_dir, args.author)
