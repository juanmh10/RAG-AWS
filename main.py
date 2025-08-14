#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Flask RAG Híbrido (EC2 t3.micro) com S3 + FAISS + Bedrock.
- Sessões por cookie
- Upload de PDF para S3
- Index FAISS por sessão salvo em S3
- Consulta via RetrievalQA (Bedrock Chat + Embeddings)
"""

import os
import io
import json
import uuid
import time
import shutil
import logging
import tempfile
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError

from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template, session
from flask_cors import CORS
from werkzeug.utils import secure_filename

# LangChain
from langchain.chains import RetrievalQA
from langchain_aws import BedrockEmbeddings, ChatBedrock
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import (
    CallbackManagerForRetrieverRun,
    AsyncCallbackManagerForRetrieverRun,
)

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
logging.basicConfig(level=logging.INFO)

AWS_REGION = os.getenv("AWS_REGION_NAME", "us-east-1")
S3_PDF_BUCKET = os.getenv("S3_PDF_BUCKET")
S3_INDEX_BUCKET = os.getenv("S3_INDEX_BUCKET")

# Modelos
EMBED_MODEL_ID = os.getenv("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
# pode ser um inference profile (ex.: us.anthropic.claude-3-7-sonnet-20250219-v1:0)
LLM_MODEL_ID = os.getenv("LLM_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

# Limites e split
MAX_TOKENS_PER_SESSION = int(os.getenv("MAX_TOKENS_PER_SESSION", "10000"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "512"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))

_missing = [k for k in ("S3_PDF_BUCKET", "S3_INDEX_BUCKET") if not os.getenv(k)]
if _missing:
    raise RuntimeError(f"Variáveis ausentes: {', '.join(_missing)}")

# ------------------------------------------------------------------------------
# Flask
# ------------------------------------------------------------------------------

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(24)
CORS(app, supports_credentials=True)

# ------------------------------------------------------------------------------
# AWS clients
# ------------------------------------------------------------------------------

s3_client = boto3.client("s3", region_name=AWS_REGION)
bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

embeddings = BedrockEmbeddings(model_id=EMBED_MODEL_ID, client=bedrock_client)

# Se usar Claude 3.7 Sonnet via inference profile, passe provider="anthropic"
llm = ChatBedrock(
    model_id=LLM_MODEL_ID,
    client=bedrock_client,
    provider="anthropic",
    model_kwargs={"max_tokens": MAX_OUTPUT_TOKENS, "temperature": 0.2},
)

# ------------------------------------------------------------------------------
# Prompt
# ------------------------------------------------------------------------------

SYSTEM_PROMPT = """Você responde SOMENTE com base no contexto fornecido.
Se a resposta exata não estiver explícita, use evidências relacionadas do contexto para inferir a melhor resposta possível.
Apenas declare "não há evidência suficiente" se realmente não houver nada relevante no contexto.
Seja direto e cite termos do documento quando útil.
"""

# ------------------------------------------------------------------------------
# Utilidades S3 / sessão
# ------------------------------------------------------------------------------

def _status_key(session_id: str) -> str:
    return f"{session_id}/status.json"

def _index_keys(session_id: str):
    return (f"{session_id}/index.faiss", f"{session_id}/index.pkl")

def _session_prefix(session_id: str) -> str:
    return f"{session_id}/"

def write_status_to_s3(session_id: str, status: str, extra: Optional[dict] = None):
    payload = {"status": status, "ts": int(time.time())}
    if extra:
        payload.update(extra)
    body = json.dumps(payload).encode("utf-8")
    s3_client.put_object(Bucket=S3_INDEX_BUCKET, Key=_status_key(session_id), Body=body)

def read_status_from_s3(session_id: str) -> Optional[dict]:
    try:
        obj = s3_client.get_object(Bucket=S3_INDEX_BUCKET, Key=_status_key(session_id))
        return json.loads(obj["Body"].read().decode("utf-8"))
    except ClientError:
        return None

def cleanup_session_resources(session_id: str):
    for bucket in (S3_PDF_BUCKET, S3_INDEX_BUCKET):
        try:
            paginator = s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=_session_prefix(session_id)):
                for item in page.get("Contents", []):
                    s3_client.delete_object(Bucket=bucket, Key=item["Key"])
            app.logger.info(f"Limpeza concluída em s3://{bucket}/{_session_prefix(session_id)}")
        except ClientError as e:
            app.logger.error(f"Falha limpando bucket {bucket}: {e}")

# ------------------------------------------------------------------------------
# FAISS <-> S3
# ------------------------------------------------------------------------------

def save_faiss_to_s3(vs: FAISS, session_id: str):
    tmp_dir = tempfile.mkdtemp()
    try:
        vs.save_local(tmp_dir)
        key_faiss, key_pkl = _index_keys(session_id)
        s3_client.upload_file(os.path.join(tmp_dir, "index.faiss"), S3_INDEX_BUCKET, key_faiss)
        s3_client.upload_file(os.path.join(tmp_dir, "index.pkl"), S3_INDEX_BUCKET, key_pkl)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def load_faiss_from_s3(session_id: str) -> Optional[FAISS]:
    tmp_dir = tempfile.mkdtemp()
    key_faiss, key_pkl = _index_keys(session_id)
    try:
        s3_client.download_file(S3_INDEX_BUCKET, key_faiss, os.path.join(tmp_dir, "index.faiss"))
        s3_client.download_file(S3_INDEX_BUCKET, key_pkl, os.path.join(tmp_dir, "index.pkl"))
        vs = FAISS.load_local(tmp_dir, embeddings, allow_dangerous_deserialization=True)
        return vs
    except ClientError as e:
        app.logger.error(f"Falha ao baixar índice da sessão {session_id}: {e}")
        return None

# ------------------------------------------------------------------------------
# Retriever compatível (não usado por padrão, mas mantido)
# ------------------------------------------------------------------------------

class ManualRetriever(BaseRetriever):
    def __init__(self, docs: List[Document]):
        super().__init__()
        self.docs = docs

    def _get_relevant_documents(
        self, query: str, *, run_manager: Optional[CallbackManagerForRetrieverRun] = None
    ) -> List[Document]:
        return self.docs

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: Optional[AsyncCallbackManagerForRetrieverRun] = None
    ) -> List[Document]:
        return self.docs

# ------------------------------------------------------------------------------
# Pipelines
# ------------------------------------------------------------------------------

def build_text_chunks_from_pdf(local_pdf_path: str) -> List[Document]:
    loader = PyPDFLoader(local_pdf_path)
    docs = loader.load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    return splitter.split_documents(docs)

def index_pdf_from_stream(file_stream: io.BytesIO, filename: str, session_id: str) -> dict:
    safe_name = secure_filename(filename) or "documento.pdf"
    pdf_key = f"{_session_prefix(session_id)}{uuid.uuid4()}-{safe_name}"

    data = file_stream.getvalue()  # lê uma vez

    # Upload do PDF
    s3_client.put_object(Bucket=S3_PDF_BUCKET, Key=pdf_key, Body=data)
    app.logger.info(f'Arquivo {filename} enviado para s3://{S3_PDF_BUCKET}/{pdf_key}')

    # Parsing local
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(safe_name)[1]) as tf:
        tf.write(data)
        local_pdf = tf.name

    try:
        docs = build_text_chunks_from_pdf(local_pdf)
        vs = FAISS.from_documents(docs, embeddings)
        save_faiss_to_s3(vs, session_id)
        return {"pdf_key": pdf_key, "chunks": len(docs)}
    finally:
        try:
            os.remove(local_pdf)
        except Exception:
            pass

def get_qa_chain_for_session(session_id: str) -> Optional[RetrievalQA]:
    vs = load_faiss_from_s3(session_id)
    if not vs:
        return None

    # Recall maior na primeira busca
    retriever = vs.as_retriever(search_kwargs={"k": 6})

    from langchain.prompts import PromptTemplate
    prompt = PromptTemplate(
        input_variables=["context", "question"],
        template=(
            f"{SYSTEM_PROMPT}\n\n"
            "Contexto:\n{context}\n\n"
            "Pergunta: {question}\n\n"
            "Resposta:"
        ),
    )
    chain = RetrievalQA.from_chain_type(
        llm=llm,
        retriever=retriever,
        chain_type="stuff",
        return_source_documents=False,
        chain_type_kwargs={"prompt": prompt, "document_variable_name": "context"},
    )
    return chain

# ------------------------------------------------------------------------------
# Sessão
# ------------------------------------------------------------------------------

@app.before_request
def ensure_session():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
        session["token_count"] = 0
        session["index_ready"] = False

# ------------------------------------------------------------------------------
# Rotas
# ------------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/debug/session")
def debug_session():
    sid = session.get("session_id")
    return jsonify({"sid": sid, "status": read_status_from_s3(sid)})

@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    f = request.files["file"]
    if not f or f.filename == "":
        return jsonify({"error": "Arquivo inválido."}), 400

    sid = session.get("session_id")
    try:
        write_status_to_s3(sid, "uploaded", {"filename": f.filename})
    except Exception as e:
        app.logger.warning(f"Não foi possível gravar status uploaded: {e}")

    try:
        stream = io.BytesIO(f.read())
        info = index_pdf_from_stream(stream, f.filename, sid)
        try:
            write_status_to_s3(sid, "ready", {"pdf_key": info["pdf_key"]})
        except Exception as e:
            app.logger.warning(f"Não foi possível gravar status ready: {e}")

        session["index_ready"] = True
        return jsonify({"ok": True, "status": "ready", **info})
    except Exception as e:
        app.logger.exception(f"Erro no processamento do upload para a sessão {sid}: {e}")
        try:
            write_status_to_s3(sid, "error", {"message": str(e)})
        except Exception:
            pass
        return jsonify({"error": "Falha ao processar o PDF"}), 500

@app.route("/status", methods=["GET"])
def status():
    sid = session.get("session_id")
    if not sid:
        return jsonify({"status": "no_session"})
    st = read_status_from_s3(sid)
    if not st:
        return jsonify({"status": "uploaded"})
    return jsonify(st)

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = data.get("question") or data.get("q")
    if not question:
        return jsonify({"error": "Pergunta não fornecida."}), 400

    sid = session.get("session_id")
    if not sid:
        return jsonify({"error": "Sessão ausente."}), 400

    st = read_status_from_s3(sid)
    status_val = (st or {}).get("status", "uploaded")
    if status_val != "ready":
        return jsonify({"error": "Índice não está pronto. Aguarde."}), 409

    if session.get("token_count", 0) >= MAX_TOKENS_PER_SESSION:
        cleanup_session_resources(sid)
        session.clear()
        return jsonify({"error": "Limite de tokens atingido. Sessão reiniciada."}), 413

    chain = get_qa_chain_for_session(sid)
    if not chain:
        return jsonify({"error": "Índice indisponível nesta sessão."}), 500

    try:
        app.logger.info(f"[CHAT] sid={sid} qlen={len(question)}")
        result = chain.invoke({"query": question})
        answer = result["result"] if isinstance(result, dict) else str(result)
        session["token_count"] = int(session.get("token_count", 0)) + len(question.split()) + len(answer.split())
        app.logger.info(f"[CHAT] ok sid={sid} alen={len(answer)}")
        return jsonify({"answer": answer, "reply": answer})
    except Exception as e:
        app.logger.exception(f"[CHAT] fail sid={sid}: {e}")
        return jsonify({"error": "Falha ao gerar resposta"}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
