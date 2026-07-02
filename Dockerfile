# prepared for Oracle Cloud deployment after the hackathon

FROM python:3.12-slim

WORKDIR /app

# System deps for torch/sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-create runtime directories
RUN mkdir -p logs reports chroma_db lab_notes emails papers data

# Ingest policy PDFs into ChromaDB at build time
RUN python3 data_ingestor.py

EXPOSE 8000 8001 8002 8004 8005 8006

CMD ["bash", "run_swarm.sh"]
