import os
import pymupdf as fitz
from docx import Document
from pathlib import Path

def text_extraction(file_path, file_extension):
    if file_extension == ".txt":
        with open(file_path,"r",encoding="utf-8",errors = "ignore") as fp:
            text = fp.read() 
            return text   
    elif file_extension == ".pdf":
        with fitz.open(file_path) as fp:
            text = ""
            for j in fp:
                text += j.get_text()
        return text
    elif file_extension == ".docx":
        doc = Document(file_path)
        text = ""
        for para in doc.paragraphs:
            text += para.text + "\n"
        return text
    else:
        print(f"File format not supported: {file_path}")
        return

 
s = Path(__file__).parent / "sample_docs"


def load_folder(s):
    list_doc = os.listdir(s)
    list_doc = sorted(list_doc)
    documents = {}
    for i in list_doc:
        file_ex = os.path.splitext(i)[1].lower()
        file_path = os.path.join(s,i) 
        documents[i] = text_extraction(file_path,file_ex)
    return documents

def load_file(file_path):
    file_name = os.path.basename(file_path)
    file_extension = os.path.splitext(file_name)[1].lower()

    documents = {}
    documents[file_name] = text_extraction(file_path, file_extension)

    return documents