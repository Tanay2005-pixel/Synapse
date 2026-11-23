
from pgvector.psycopg import register_vector
from src.py.db_connection import get_db_connection


def save_semantic_chunks(file_id, chunks, embeddings):
    connection = get_db_connection()
    register_vector(connection)

    try:
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM semantic_chunks WHERE file_id = %s",
                (file_id,)
            )

            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                cur.execute(
                    """
                    INSERT INTO semantic_chunks
                    (file_id, chunk_index, chunk_text, embedding)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (file_id, index, chunk, embedding)
                )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()



def search_semantic_chunks(query_embedding, file_ids, top_k=5):
    if not file_ids:
        return []

    connection = get_db_connection()
    register_vector(connection)

    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT
                    file_id,
                    chunk_text,
                    1 - (embedding <=> %s) AS score
                FROM semantic_chunks
                WHERE file_id = ANY(%s)
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (query_embedding, file_ids, query_embedding, top_k)
            )

            return cur.fetchall()

    finally:
        connection.close()

def delete_semantic_chunks(file_id):
    connection = get_db_connection()
    register_vector(connection)

    try:
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM semantic_chunks WHERE file_id = %s",
                (file_id,)
            )

        connection.commit()

    finally:
        connection.close()
