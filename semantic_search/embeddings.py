from .cleaning_chunking import clean_doc
from sentence_transformers import SentenceTransformer
from .reading_files import load_file

model = SentenceTransformer("all-MiniLM-L6-v2")

def index_building(file_path):
    documents = load_file(file_path)
    chunked_documents = clean_doc(documents)

    chunks = []
    file_name_chunks = []
    
    for name,chunk in chunked_documents.items():
        for i in chunk:
            chunks.append(i)
            file_name_chunks.append(name)

    embeddings = model.encode(chunks)
    return chunks,file_name_chunks,embeddings