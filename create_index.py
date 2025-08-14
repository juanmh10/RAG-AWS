#!/usr/bin/env python
# -*- coding: utf-8 -*-

# app/create_index.py

"""
Script para criar o índice vetorial FAISS. 
Ele baixa os PDFs de um bucket S3 um por um para um diretório local temporário,
processa-os com o leve PyPDFLoader e, em seguida, faz o upload do índice FAISS finalizado
para outro bucket S3.
"""

from langchain_aws import BedrockEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
import re
import json
import boto3
import os

# --- CONFIGURAÇÕES ---
os.environ["AWS_REGION"] = "us-east-1"
VECTOR_BUCKET = "rag-ec2-vector"
INDEX_FILE_NAME = "index-titan-sonnet"
DOC_BUCKET = "rag-docs-juan"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
LOCAL_PDF_DIR = "/tmp/pdf_downloads/"
LOCAL_INDEX_PATH = "/tmp/faiss_index"


def clean_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()

def create_and_upload_index():
    """Orquestra o download, processamento e upload do índice."""
    print("---> Iniciando a criação do índice vetorial...")
    s3_client = boto3.client("s3")
    all_docs = []

    # 1. Listar e baixar PDFs do S3, processando um por um
    print(f"[1/4] Lendo arquivos PDF do bucket: {DOC_BUCKET}")
    os.makedirs(LOCAL_PDF_DIR, exist_ok=True)
    
    try:
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=DOC_BUCKET)
        pdf_keys = [obj['Key'] for page in pages for obj in page.get('Contents', []) if obj['Key'].lower().endswith('.pdf')]
    except Exception as e:
        print(f"ERRO ao listar arquivos no bucket {DOC_BUCKET}. Verifique as permissões da IAM Role. Erro: {e}")
        return

    if not pdf_keys:
        print(f"ERRO: Nenhum arquivo .pdf encontrado no bucket {DOC_BUCKET}.")
        return

    print(f"Encontrados {len(pdf_keys)} arquivos PDF. Iniciando o processamento...")
    for key in pdf_keys:
        local_pdf_path = os.path.join(LOCAL_PDF_DIR, os.path.basename(key))
        print(f"  - Processando: {key}")
        try:
            s3_client.download_file(DOC_BUCKET, key, local_pdf_path)
            # Tenta PyPDF como primário
            try:
                loader = PyPDFLoader(local_pdf_path)
                docs = loader.load()
            except Exception:
                # Fallback para pdfminer (importa dinamicamente para não obrigar a dependência em runtime do servidor)
                try:
                    import pdfminer.high_level as pdfminer_high
                    text = pdfminer_high.extract_text(local_pdf_path)
                    docs = [ { 'page_content': clean_text(text), 'metadata': {} } ]
                except Exception as e:
                    print(f"    AVISO: Falha ao extrair texto com fallback para {key}. Erro: {e}")
                    docs = []

            # Normaliza e converte para o formato de Document do LangChain
            for d in docs:
                content = d.page_content if hasattr(d, 'page_content') else d.get('page_content', '')
                content = clean_text(content)
                if content:
                    all_docs.append(content)
        except Exception as e:
            print(f"    AVISO: Falha ao processar o arquivo {key}. Erro: {e}")
        finally:
            if os.path.exists(local_pdf_path):
                os.remove(local_pdf_path) # Limpa o arquivo temporário

    if not all_docs:
        print("ERRO: Nenhum documento pôde ser carregado. Verifique os arquivos PDF.")
        return

    # 2. Dividir os documentos agregados
    print(f"\n[2/4] Dividindo {len(all_docs)} páginas em chunks...")
    # Heurística: chunk_size entre 300-600 tokens. Usamos caracteres como proxy.
    splitter = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=240)
    # O splitter espera objetos Document; converte
    doc_objs = [ { 'page_content': d, 'metadata': {} } for d in all_docs ]
    splitted_docs = splitter.split_documents(doc_objs)
    print(f"Documentos divididos em {len(splitted_docs)} chunks.")

    # 3. Criar o índice FAISS
    print("\n[3/4] Criando índice FAISS com embeddings Titan...")
    embeddings = BedrockEmbeddings(model_id=EMBED_MODEL_ID)
    vectorstore = FAISS.from_documents(splitted_docs, embeddings)
    os.makedirs(LOCAL_INDEX_PATH, exist_ok=True)
    vectorstore.save_local(LOCAL_INDEX_PATH, index_name=INDEX_FILE_NAME)
    print(f"Índice salvo localmente.")

    # 4. Fazer upload do índice para o S3
    print(f"\n[4/4] Fazendo upload do índice para o bucket {VECTOR_BUCKET}...")
    try:
        faiss_file = os.path.join(LOCAL_INDEX_PATH, f"{INDEX_FILE_NAME}.faiss")
        pkl_file = os.path.join(LOCAL_INDEX_PATH, f"{INDEX_FILE_NAME}.pkl")
        s3_client.upload_file(faiss_file, VECTOR_BUCKET, os.path.basename(faiss_file))
        s3_client.upload_file(pkl_file, VECTOR_BUCKET, os.path.basename(pkl_file))
        print("\n*** Processo concluído! Índice vetorial está pronto para uso. ***")
    except Exception as e:
        print(f"ERRO durante o upload do índice para o S3: {e}")

if __name__ == "__main__":
    create_and_upload_index()
