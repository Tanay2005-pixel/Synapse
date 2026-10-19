#library that is used to work with files n folders
import os
import pymupdf as fitz
from docx import Document
from pathlib import Path
#used to get the path of the current folder

def text_extraction(file_path, file_extension):
    if file_extension == ".txt":
        #incase python cannot decode a char instead of giving an error the program continues running
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

#finds the path of the current file(__file__)n then parent gets the folder the current file is under to form a complete path 
s = Path(__file__).parent / "sample_docs"


def load_folder(s):
    list_doc = os.listdir(s)
    list_doc = sorted(list_doc)
    #making a dictionary to store the file contents with their name
    documents = {}
    for i in list_doc:
        #os.path.splitext is used to split the file name from its extension
        #the extension here is being used to identify the file type
        file_ex = os.path.splitext(i)[1].lower()
        file_path = os.path.join(s,i)
        #used to join the path n file name to get the complete path of the file
        #finding the file path helps to retrive the file content then extract it further 
        documents[i] = text_extraction(file_path,file_ex)
    return documents
