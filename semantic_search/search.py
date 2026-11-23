from pathlib import Path
from sentence_transformers import util

from embeddings import model, index_building


def file_ranking(scores, chunks, file_name_chunks, top_k=3):

    ranking = sorted(
        zip(scores, chunks, file_name_chunks),
        key=lambda x: x[0],
        reverse=True
    )
    
    return [
            (name, score, chunk) 
            for score, chunk, name in ranking[:top_k]
            ]


def search(query, chunks, file_name_chunks, embeddings, top_k=3):
    embedded_query = model.encode(query)
    scores = util.cos_sim(
        embedded_query, 
        embeddings,
        )[0].tolist()
    
    return file_ranking(scores, chunks, file_name_chunks, top_k)

