import os
from langchain_community.document_loaders import PyPDFDirectoryLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma # Note: Updated import for newer LangChain versions

# --- BULLETPROOF PATH RESOLUTION ---
# This forces Python to find the directory exactly where this script lives.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_BASE_DIR = os.path.join(SCRIPT_DIR, "knowledge_base")
DB_DIR = os.path.join(SCRIPT_DIR, "chroma_db")

def ingest_documents():
    print(f"[*] Scanning absolute directory: {KNOWLEDGE_BASE_DIR}...")

    # 1. Load all PDFs in the directory
    loader = PyPDFDirectoryLoader(KNOWLEDGE_BASE_DIR)
    documents = loader.load()

    if not documents:
        print("[!] No documents found. Ensure PDFs are in the knowledge_base folder.")
        return

    print(f"[*] Loaded {len(documents)} pages.")

    # 2. Chunking Strategy
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n## ", "\n### ", "\n\n", "\n", ".", " "]
    )

    chunks = text_splitter.split_documents(documents)
    print(f"[*] Split into {len(chunks)} semantic chunks.")

    # 3. Initialize Local Embeddings
    print("[*] Initializing local embedding model (all-MiniLM-L6-v2)...")
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

    # 4. Ingest and Persist to ChromaDB
    print(f"[*] Building vector database at {DB_DIR}...")

    vector_db = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=DB_DIR
    )

    print("[*] Ingestion complete. The Compliance Oracle is ready to query.")

if __name__ == "__main__":
    # Ensure directories exist before running
    os.makedirs(KNOWLEDGE_BASE_DIR, exist_ok=True)
    ingest_documents()