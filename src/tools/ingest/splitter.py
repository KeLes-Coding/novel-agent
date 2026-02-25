import os
import json
import argparse
import re
from pathlib import Path
from typing import List, Dict, Any

def chunk_sliding_window(text: str, window_size: int = 300, overlap: int = 50) -> List[str]:
    """
    Strategy A: Generic sliding window chunks.
    Window size and overlap are in characters (roughly).
    We try to break at paragraph boundaries if possible near the limit.
    """
    # Simple para-based accumulation
    paragraphs = text.split('\n\n')
    chunks = []
    
    current_chunk = ""
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
            
        if len(current_chunk) + len(para) > window_size:
            # Flush current
            if current_chunk:
                chunks.append(current_chunk)
            
            # Start new. Handle overlap?
            # For simplicity:
            # If current_chunk is empty, para is just too long -> keep it as one (or hard split)
            # Efficient "RAG-friendly" way:
            # Overlap is tricky with discrete paragraphs. 
            # Let's keep last paragraph of previous chunk if possible?
            
            # Simplified Logic:
            # Just accumulate until > size.
            if len(para) > window_size:
                # If single para is HUGE, split it hard?
                # For novels, paras are rarely > 1000 chars. Let's keep it whole.
                current_chunk = para
            else:
                current_chunk = para
        else:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
                
    if current_chunk:
        chunks.append(current_chunk)
        
    return chunks

def is_elite_chunk(text: str) -> Dict[str, Any]:
    """
    Strategy B: Elite chunk filtering.
    Returns dict with 'is_elite': bool, 'type': str (dialogue, description, etc)
    """
    # 1. Dialogue Density
    # Count quotes
    quotes_count = text.count('“') + text.count('”') + text.count('"')
    total_len = len(text)
    if total_len < 50: 
        return {"is_elite": False}
        
    # Heuristic: If > 1/3 of text is inside quotes? Or just quote count relative to length
    # A dense dialogue scene usually has short lines.
    if quotes_count > 4 and (quotes_count * 10 / total_len) > 0.2:
        return {"is_elite": True, "tags": ["dialogue"]}
        
    # 2. Description Density (Adjectives/Imageries)
    # Without jieba, we look for "adj + 的" or sensory words
    # This is a weak heuristic but fast.
    sensory_words = ["仿佛", "宛如", "漆黑", "璀璨", "冰冷", "炙热", "轰鸣", "寂静", "深邃", "刺眼"]
    score = 0
    for w in sensory_words:
        if w in text:
            score += 1
            
    # "的" is very common, but "XX的YY" pattern hints at description.
    de_count = text.count("的")
    if (de_count / total_len) > 0.08: # High usage of adjectives usually
        score += 2
        
    if score >= 3:
        return {"is_elite": True, "tags": ["description"]}
        
    # 3. Inner Monologue
    monologue_patterns = ["想道", "心想", "意识到", "暗道", "觉得"]
    for p in monologue_patterns:
        if p in text:
             return {"is_elite": True, "tags": ["monologue"]}

    return {"is_elite": False}

def get_chunks(text: str, author: str = "Unknown", book: str = "Unknown") -> Dict[str, List[Dict[str, Any]]]:
    """
    Core splitting logic. Returns a dict mapping 'generic' and 'elite' to their respective lists of records.
    """
    # 1. Generic Chunks
    generic_chunks = chunk_sliding_window(text)
    
    # 2. Elite Chunks
    elite_output = []
    generic_output = []
    
    for i, c in enumerate(generic_chunks):
        # Build generic record
        rec = {
            "text": c,
            "meta": {
                "author": author,
                "book": book,
                "chunk_id": i,
                "type": "generic"
            }
        }
        generic_output.append(rec)
        
        # Check elite
        check = is_elite_chunk(c)
        if check["is_elite"]:
            rec_elite = rec.copy()
            rec_elite["meta"]["type"] = "elite"
            rec_elite["meta"]["tags"] = check.get("tags", [])
            elite_output.append(rec_elite)
            
    return {
        "generic": generic_output,
        "elite": elite_output
    }

def process_splitting(input_path: str, output_dir: str, author: str = "Unknown", book: str = "Unknown"):
    """
    Standalone file processing: Read -> Split -> Write (Overwrite mode)
    """
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        print(f"Read error: {e}")
        return

    result = get_chunks(text, author, book)
    
    # Save
    os.makedirs(output_dir, exist_ok=True)
    
    gen_path = os.path.join(output_dir, "style_chunks.jsonl")
    elite_path = os.path.join(output_dir, "style_elite.jsonl")
    
    with open(gen_path, 'w', encoding='utf-8') as f:
        for r in result["generic"]:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
            
    with open(elite_path, 'w', encoding='utf-8') as f:
        for r in result["elite"]:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
            
    print(f"Processed {len(result['generic'])} generic chunks.")
    print(f"Selected {len(result['elite'])} elite chunks.")
    print(f"Saved to {output_dir}")

def main():
    parser = argparse.ArgumentParser(description="Split novel text into chunks.")
    parser.add_argument("--input", "-i", type=str, required=True, help="Cleaned input text")
    parser.add_argument("--output_dir", "-o", type=str, required=True, help="Output directory")
    parser.add_argument("--author", type=str, default="Unknown")
    parser.add_argument("--book", type=str, default="Unknown")
    
    args = parser.parse_args()
    process_splitting(args.input, args.output_dir, args.author, args.book)

if __name__ == "__main__":
    main()
