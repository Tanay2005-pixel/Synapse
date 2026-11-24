from .embeddings import index_building, model
from src.py.semantic_db import (
    save_semantic_chunks,
    search_semantic_chunks,
    delete_semantic_chunks
)
from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx"}

def add_file(file_id, file_path):
    extension = Path(file_path).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        return

    chunks, file_names, embeddings = index_building(file_path)

    save_semantic_chunks(file_id, chunks, embeddings)

def remove_file(file_id):
    delete_semantic_chunks(file_id)

def search_files(query, file_ids, top_k=5):
    query_embedding = model.encode(query)

    matches = search_semantic_chunks(
        query_embedding,
        file_ids,
        top_k
    )

    results = []

    for file_id, chunk, score in matches:
        results.append({
            "file_id": file_id,
            "file_name": None,
            "score": float(score),
            "snippet": chunk
        })

    return results

