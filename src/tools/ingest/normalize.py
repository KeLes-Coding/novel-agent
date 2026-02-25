import os
import re
import argparse
import unicodedata
from pathlib import Path

def normalize_text(text: str) -> str:
    """
    Standardize text:
    1. Unicode normalization (NFKC)
    2. Remove invisible characters
    """
    # Unicode normalize
    text = unicodedata.normalize('NFKC', text)
    
    # Remove zero-width spaces and other invisible control chars (keep newlines/tabs)
    text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]', '', text)
    
    return text

def remove_noise(text: str) -> str:
    """
    Remove common ads and site watermarks.
    """
    patterns = [
        r"^\s*本章完\s*$",
        r"^\s*求收藏.*$",
        r"^\s*求推荐.*$",
        r"^\s*PS[:：].*$",
        r"^\s*（本章完）\s*$",
        r"^\s*.*(微信|公众号|关注|打赏|月票).*\s*$",
        # Add more regex patterns here as needed based on corpus analysis
    ]
    
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        is_noise = False
        for p in patterns:
            if re.search(p, line, re.IGNORECASE):
                is_noise = True
                break
        if not is_noise:
            cleaned_lines.append(line)
            
    return '\n'.join(cleaned_lines)

def merge_broken_lines(text: str) -> str:
    """
    Heuristic merge of broken lines.
    If a line does NOT end with a sentence-ending punctuation, 
    it might be a broken line that should be merged with the next one.
    
    Sentence endings: 。！？!?”"…
    """
    lines = text.split('\n')
    merged_lines = []
    
    # Common ending punctuation in Chinese and English novels
    end_puncts = set(['。', '！', '？', '!', '?', '”', '"', '…', '......', '——', '—'])
    
    current_buffer = ""
    
    for i, line in enumerate(lines):
        striped = line.strip()
        if not striped:
            # Empty line -> assume paragraph break
            if current_buffer:
                merged_lines.append(current_buffer)
                current_buffer = ""
            # Preserve empty lines as paragraph separators if desired, 
            # OR just ignore them. Usually we want standard paragraph spacing.
            # Let's add an empty line to denote paragraph break if we just flushed.
            continue
            
        # If we have a buffer, append current line to it
        if current_buffer:
            current_buffer += striped
        else:
            current_buffer = striped
            
        # Check if we should flush
        # Heuristic: If it ends with punctuation, it MIGHT be a full paragraph.
        # But sometimes a paragraph is long.
        # Strict approach: Merge until we see a blank line in original text?
        # Novel text usually has blank lines between paragraphs.
        # Let's rely on the original blank lines. The logic above handles blank lines.
        # If the original text has NO blank lines between paragraphs and uses indentation... that's harder.
        
        # Let's try the "Punctuation" heuristic for safety.
        # If current line ends with valid punctuation, it COULD be end of paragraph.
        # But if next line starts with indentation, it is definitely a new paragraph.
        
        # For now, let's Stick to: "Blank lines separate paragraphs".
        # If lines are adjacent without blank line:
        # Check if previous line ended with punctuation. If NOT, merge.
        # If YES, keep separate?
        # Many txt files are hard-wrapped at 80 chars. In that case, lines don't end in punctuation.
        
        pass

    # Re-implementation for specific "Hard Wrap" fixing:
    # 1. Split by original newlines.
    # 2. If a line does NOT end in punctuation, merge with next.
    # 3. If a line ends in punctuation, keep as is (or merge if next line is not indented? - too complex for now).
    
    # Revised Logic:
    transformed_lines = []
    temp_buf = ""
    
    for line in lines:
        s = line.strip()
        if not s:
            # Blank line -> flush buffer
            if temp_buf:
                transformed_lines.append(temp_buf)
                temp_buf = ""
            continue
        
        if not temp_buf:
            temp_buf = s
        else:
            # We have a buffer. Should we merge 's' into it?
            # Check if temp_buf ends with punctuation
            if any(temp_buf.rstrip().endswith(p) for p in end_puncts):
                # Ends with punctuation. Likely a finished sentence/paragraph.
                # But wait, maybe the next line is just a continuation?
                # Safest bet for novels: Assume each non-empty block roughly correlates to a paragraph,
                # UNLESS it clearly looks like hard-wrapping (no punctuation at end).
                
                # If it ends with punctuation, we flush the buffer and start new.
                transformed_lines.append(temp_buf)
                temp_buf = s
            else:
                # Does NOT end with punctuation. Almost certainly a broken line.
                temp_buf += s
    
    if temp_buf:
        transformed_lines.append(temp_buf)
        
    return '\n\n'.join(transformed_lines)

# === Format Readers ===
import json

try:
    import docx
except ImportError:
    docx = None

try:
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup
except ImportError:
    ebooklib = None
    BeautifulSoup = None

def read_text_file(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        # Fallback to GB18030 for Chinese texts
        with open(path, 'r', encoding='gb18030', errors='ignore') as f:
            return f.read()

def read_docx(path: str) -> str:
    if not docx:
        raise ImportError("python-docx not installed. pip install python-docx")
    doc = docx.Document(path)
    # Join with newlines
    return '\n'.join([p.text for p in doc.paragraphs])

def read_epub(path: str) -> str:
    if not ebooklib or not BeautifulSoup:
        raise ImportError("EbookLib or BeautifulSoup4 not installed. pip install EbookLib BeautifulSoup4")
    
    book = epub.read_epub(path)
    chapters = []
    
    # Iterate items
    # Typically ITEM_DOCUMENT are the text chapters
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            # Use BS4 to strip HTML
            soup = BeautifulSoup(item.get_content(), 'html.parser')
            text = soup.get_text('\n')
            chapters.append(text)
            
    return '\n\n'.join(chapters)

def read_json(path: str) -> str:
    """
    Auto-detect logic for JSON structure.
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    if isinstance(data, str):
        return data
    elif isinstance(data, list):
        # List of strings?
        return '\n'.join([str(x) for x in data])
    elif isinstance(data, dict):
        # Try common keys
        for k in ['content', 'text', 'body', 'chapters']:
            if k in data:
                val = data[k]
                if isinstance(val, list):
                     return '\n'.join([str(x) for x in val])
                return str(val)
        # Fallback dump
        return json.dumps(data, ensure_ascii=False, indent=2)
    return ""

def process_file(input_path: str, output_path: str):
    # Incremental check
    if os.path.exists(output_path):
        in_mtime = os.path.getmtime(input_path)
        out_mtime = os.path.getmtime(output_path)
        if out_mtime >= in_mtime:
            # print(f"Skipping {input_path} (Up to date)")
            return

    print(f"Processing {input_path} ...")
    
    ext = os.path.splitext(input_path)[1].lower()
    raw = ""
    
    try:
        if ext == '.docx':
            raw = read_docx(input_path)
            # print("Detected DOCX format.")
        elif ext == '.epub':
            raw = read_epub(input_path)
            # print("Detected EPUB format.")
        elif ext == '.json':
            raw = read_json(input_path)
            # print("Detected JSON format.")
        else:
            raw = read_text_file(input_path)
    except Exception as e:
        print(f"Error reading file {input_path}: {e}")
        return
            
    text = normalize_text(raw)
    text = remove_noise(text)
    text = merge_broken_lines(text)
    
    # Ensure dir
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(text)
        
    print(f"Saved to {output_path}")

def main():
    parser = argparse.ArgumentParser(description="Normalize novel text corpus.")
    parser.add_argument("--input", "-i", type=str, required=True, help="Input file path or directory")
    parser.add_argument("--output", "-o", type=str, required=True, help="Output file path or directory")
    
    args = parser.parse_args()
    
    in_path = Path(args.input)
    out_path = Path(args.output)
    
    if in_path.is_file():
        # Single file mode
        # If output is dir, use input filename
        if out_path.is_dir() or (not out_path.suffix): # Treat as dir
            final_out = out_path / (in_path.stem + "_clean.txt")
        else:
            final_out = out_path
        process_file(str(in_path), str(final_out))
        
    elif in_path.is_dir():
        # Batch mode
        if out_path.is_file():
            print("Error: Input is a directory, but output is a file.")
            return
            
        # Recursive scan for supported extensions
        supported_exts = {'.txt', '.docx', '.epub', '.json'}
        files = []
        for root, _, filenames in os.walk(in_path):
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext in supported_exts:
                     files.append(os.path.join(root, name))
        
        print(f"Found {len(files)} files to process in {in_path}")
        
        for f in files:
            # Replicate structure in output
            rel = os.path.relpath(f, in_path)
            # Change extension to .txt
            rel_stem = os.path.splitext(rel)[0]
            dest = out_path / (rel_stem + ".txt")
            process_file(str(f), str(dest))
    else:
        print(f"Input {args.input} not found.")

if __name__ == "__main__":
    main()
