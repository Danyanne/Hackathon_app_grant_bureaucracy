import os
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# Absolute path resolution (same as the ingestor)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(SCRIPT_DIR, "chroma_db")

def probe_database(query_text: str):
    print(f"[*] Loading database from {DB_DIR}...")

    # 1. We MUST use the exact same embedding model used during ingestion
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

    # 2. Connect to the existing Chroma database
    vector_db = Chroma(
        persist_directory=DB_DIR,
        embedding_function=embeddings
    )

    print(f"[*] Probing database for: '{query_text}'\n")

    # 3. Perform the search (fetch top 3 closest chunks)
    # Chroma returns L2 distance scores (lower is better/closer to the query)
    results = vector_db.similarity_search_with_score(query_text, k=3)

    if not results:
        print("[!] No results found. The database might be empty.")
        return

    # 4. Print the raw data the LLM will eventually see
    for i, (doc, score) in enumerate(results, 1):
        print(f"--- MATCH {i} | Distance Score: {score:.4f} ---")
        # Metadata shows which PDF this chunk came from
        print(f"Source: {doc.metadata.get('source', 'Unknown')} (Page {doc.metadata.get('page', 'N/A')})")
        # We print the first 300 characters of the chunk to verify it didn't slice a sentence in half
        print(f"Content: {doc.page_content[:300]}...\n")

if __name__ == "__main__":
    # Test Query 1: Should trigger the ERC grant rules
    print("=== TEST 1: ERC RULES ===")
    test_query_1 = "Can I use the grant to buy AWS cloud compute credits?"
    probe_database(test_query_1)

    print("\n" + "="*50 + "\n")

    # Test Query 2: Should trigger the Departmental rules
    print("=== TEST 2: DEPARTMENTAL RULES ===")
    test_query_2 = "What is the maximum nightly hotel rate for a conference in Europe?"
    probe_database(test_query_2)