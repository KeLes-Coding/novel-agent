import os
import json
import argparse
import chromadb
from chromadb.config import Settings
from typing import List, Dict, Any
from pathlib import Path

class StyleIndexer:
    def __init__(self, db_path: str = "data/chroma_db", collection_name: str = "style_bank"):
        # Ensure db directory exists
        os.makedirs(db_path, exist_ok=True)
        
        self.client = chromadb.PersistentClient(path=db_path)
        
        # Get or create collection
        # We use default embedding function (all-MiniLM-L6-v2) by not specifying one.
        # This requires 'sentence-transformers' or internet access to download onnx model on first run.
        # If offline, might need manual setup. Assuming standard env.
        self.collection = self.client.get_or_create_collection(name=collection_name)
        print(f"Connected to collection '{collection_name}' in '{db_path}'")

    def index_file(self, jsonl_path: str):
        """
        Read a jsonl file and ingest chunks.
        """
        print(f"Indexing {jsonl_path} ...")
        documents = []
        metadatas = []
        ids = []
        
        count = 0
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    print(f"Skipping invalid json line: {line[:50]}...")
                    continue
                
                text = data.get("text", "")
                if not text:
                    continue
                
                meta = data.get("meta", {})
                
                # Metadata flattening: Chroma metadata must be int, float, str, or bool.
                # Lists are not supported in metadata values directly in basic chroma backend usually,
                # but let's check. Latest chroma might support it? 
                # Safe bet: convert lists to string representation.
                chroma_meta = {}
                for k, v in meta.items():
                    if isinstance(v, list):
                        chroma_meta[k] = ",".join(str(x) for x in v)
                    else:
                        chroma_meta[k] = v
                
                # Add source file to meta
                chroma_meta["source_file"] = os.path.basename(jsonl_path)
                
                # ID generation: "author_book_chunkId_type"
                # If chunk_id missing, use hash or count
                chunk_id = meta.get("chunk_id", count)
                author = meta.get("author", "unknown")
                book = meta.get("book", "unknown")
                ctype = meta.get("type", "generic")
                
                doc_id = f"{author}_{book}_{ctype}_{chunk_id}"
                
                documents.append(text)
                metadatas.append(chroma_meta)
                ids.append(doc_id)
                count += 1
                
                # Batch upsert every 100
                if len(documents) >= 100:
                    self.collection.upsert(
                        documents=documents,
                        metadatas=metadatas,
                        ids=ids
                    )
                    documents = []
                    metadatas = []
                    ids = []

        # Upsert remaining
        if documents:
            self.collection.upsert(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            
        print(f"Indexed {count} documents from {jsonl_path}")

def main():
    parser = argparse.ArgumentParser(description="Index style chunks into ChromaDB.")
    parser.add_argument("--input", "-i", type=str, required=True, help="Input .jsonl file or directory containing .jsonl files")
    parser.add_argument("--db_path", type=str, default="data/chroma_db", help="Path to ChromaDB storage")
    parser.add_argument("--collection", type=str, default="style_bank", help="Collection name")
    
    args = parser.parse_args()
    
    indexer = StyleIndexer(db_path=args.db_path, collection_name=args.collection)
    
    input_path = Path(args.input)
    if input_path.is_file():
        indexer.index_file(str(input_path))
    elif input_path.is_dir():
        for f in input_path.glob("**/*.jsonl"):
            indexer.index_file(str(f))
    else:
        print(f"Input {args.input} not found.")

if __name__ == "__main__":
    main()
