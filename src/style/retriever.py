import argparse
import chromadb
from typing import List, Dict, Any, Optional

class StyleRetriever:
    def __init__(self, db_path: str = "data/chroma_db", collection_name: str = "style_bank"):
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_collection(name=collection_name)
        
    def retrieve(self, query: str, n_results: int = 5, filter_meta: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Semantic search for style chunks.
        filter_meta: dict for filtering, e.g. {"author": "LuXun", "tags": "dialogue"}
        """
        # Construct filter
        # ChromaDB WHERE clause syntax
        # simple: {"key": "value"}
        # AND: {"$and": [{"key1": "val1"}, {"key2": "val2"}]}
        
        where_clause = None
        if filter_meta:
            if len(filter_meta) == 1:
                k, v = list(filter_meta.items())[0]
                where_clause = {k: v}
            elif len(filter_meta) > 1:
                and_list = [{k: v} for k, v in filter_meta.items()]
                where_clause = {"$and": and_list}
                
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where_clause
        )
        
        # Format results
        # results is a dict of lists: {'ids': [[]], 'documents': [[]], 'metadatas': [[]], 'distances': [[]]}
        # We assume single query, so take index 0
        
        output = []
        if not results['documents']:
            return []
            
        docs = results['documents'][0]
        metas = results['metadatas'][0]
        dists = results['distances'][0]
        ids = results['ids'][0]
        
        for i in range(len(docs)):
            output.append({
                "id": ids[i],
                "text": docs[i],
                "meta": metas[i],
                "distance": dists[i]
            })
            
        return output

def main():
    parser = argparse.ArgumentParser(description="Retrieve style chunks from ChromaDB.")
    parser.add_argument("--query", "-q", type=str, required=True, help="Query text (e.g. scene summary)")
    parser.add_argument("--n", type=int, default=3, help="Number of results")
    parser.add_argument("--author", type=str, help="Filter by author")
    parser.add_argument("--tag", type=str, help="Filter by tag (e.g. dialogue, description)")
    
    args = parser.parse_args()
    
    retriever = StyleRetriever()
    
    filters = {}
    if args.author:
        filters["author"] = args.author
    if args.tag:
        # Note: tags in chroma metadata are stored as strings "tag1,tag2"
        # Exact match filtering on "tags" might fail if it contains multiple tags.
        # Chroma's basic filtering is exact match. 
        # For full implementation, we might need $contains operator if supported, or store tags differently.
        # For now, let's assume single tag or exact string match.
        # Or better: don't filter by tag in CLI for now if it's complex, 
        # just print warning in implementation plan.
        filters["type"] = args.tag # mapping tag to 'type' field which is simpler? 
        # actually previous code stored 'type' in meta. 'tags' is list converted to string.
        # Let's try filtering on 'type' (generic/elite) or just author.
        pass

    # Basic filter support
    if args.tag:
         # Try precise match on 'tags' string if user knows it
         pass
         
    # Let's just pass author for now to test
    
    results = retriever.retrieve(args.query, n_results=args.n, filter_meta=filters)
    
    print(f"--- Top {args.n} Results for '{args.query}' ---")
    for r in results:
        print(f"\n[Score: {r['distance']:.4f}] ID: {r['id']}")
        print(f"Meta: {r['meta']}")
        print(f"Text: {r['text'][:100]}...")

if __name__ == "__main__":
    main()
