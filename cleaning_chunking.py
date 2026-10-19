import os
from pathlib import Path
from reading_files import load_folder

s = Path(__file__).parent / "sample_docs"
documents = load_folder(s)

cleaned_documents = {}
chunked_documents = {}

def clean_doc(documents):
    for i in documents:
        text = documents[i].split()
        len_doc = len(text)
        cleaned_documents[i] = " ".join(text)
        chunked_documents[i] = chunk_doc(cleaned_documents[i],len_doc)
    return chunked_documents        

def chunk_doc(cleaned_documents, len_doc):
    indexes = []
    chunks = []
    chunk_size = 100
    words = cleaned_documents.split()
    indexes = list(range(0,len_doc,chunk_size))
    for i in indexes:
        chunks.append(" ".join(words[i : i + chunk_size]))
    return chunks
