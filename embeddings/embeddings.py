from cleaning_chunking import clean_doc
from pathlib import Path
from sentence_transformers import SentenceTransformer,util
from reading_files import load_folder

s = Path(__file__).parent / "sample_docs"
documents = load_folder(s)

model = SentenceTransformer("all-MiniLM-L6-v2")
chunked_documents = clean_doc(documents)

chunks = []

for i in chunked_documents:
    for j in chunked_documents[i]:
        chunks.append(j)

embeddings = model.encode(chunks)