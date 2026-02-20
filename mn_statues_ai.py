'''
# Pull the "Brain" (Llama 3.1 is great for local reasoning)
ollama pull llama3.1

# Pull the "Librarian" (mxbai-embed-large is a top-tier local embedding model)
ollama pull mxbai-embed-large


pip install ollama psycopg2-binary
'''

import ollama
import psycopg2
import json

# 1. DATABASE CONFIGURATION
# (Assumes you have Postgres + pgvector installed locally)
DB_CONFIG = {
    "dbname": "mn_legal_db",
    "user": "postgres",
    "password": "your_password",
    "host": "localhost",
    "port": "5432"
}

def get_local_embedding(text):
    """Generates a vector using the local mxbai-embed-large model."""
    response = ollama.embed(
        model="mxbai-embed-large",
        input=text
    )
    # mxbai-embed-large returns a list of embeddings; we take the first one
    return response['embeddings'][0]

def search_statutes(query_vector, limit=5):
    """Searches your local Postgres DB for the most relevant law chunks."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    # Note: mxbai-embed-large uses 1024 dimensions. 
    # Ensure your table column is VECTOR(1024)
    search_query = """
        SELECT section_id, content 
        FROM mn_statutes 
        ORDER BY embedding <=> %s::vector 
        LIMIT %s;
    """
    
    cur.execute(search_query, (query_vector, limit))
    results = cur.fetchall()
    cur.close()
    conn.close()
    return results

def ask_local_llama(user_prompt, context_chunks):
    """Sends prompt + context to the local Llama 3.1 model."""
    
    formatted_context = "\n\n".join(
        [f"--- SECTION {res[0]} ---\n{res[1]}" for res in context_chunks]
    )

    system_msg = (
        "You are a Minnesota Legal Assistant. Use ONLY the provided law excerpts. "
        "If the answer isn't there, say you don't know. Cite the Section Number."
    )

    response = ollama.chat(
        model="llama3.1",
        messages=[
            {'role': 'system', 'content': system_msg},
            {'role': 'user', 'content': f"Context:\n{formatted_context}\n\nQuestion: {user_prompt}"}
        ]
    )
    return response['message']['content']

# 2. EXECUTION
if __name__ == "__main__":
    query = input("Ask a question about MN Statutes: ")
    
    print("... Generating Local Embedding ...")
    query_vec = get_local_embedding(query)
    
    print("... Querying Local Database ...")
    context = search_statutes(query_vec)
    
    if not context:
        print("No relevant statutes found.")
    else:
        print("... Llama 3.1 is thinking ...")
        answer = ask_local_llama(query, context)
        print("\n--- LOCAL AI RESPONSE ---")
        print(answer)