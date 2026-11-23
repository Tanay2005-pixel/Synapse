from pathlib import Path
from embeddings import index_building
from search import search

dir = Path(__file__).resolve().parent
s = dir / "sample_docs"

chunks, file_name_chunks, embeddings = index_building(s)

query = input("Enter your question: ")

results = search(
    query,
    chunks,
    file_name_chunks,
    embeddings
)

for name, score, chunk in results:
    print(f"Score: {score:.4f}")
    print(f"Content: {chunk}")
    print(f"\nSource: {name}")