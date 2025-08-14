
# RAG-EC2 • Chat com Documentos usando AWS (EC2 + S3 + Bedrock + FAISS)

Aplicação de RAG (Retrieval-Augmented Generation) que permite ao usuário enviar um PDF e fazer perguntas com respostas fundamentadas no conteúdo do documento. A solução roda em uma instância EC2 e utiliza Amazon S3 para persistência, Amazon Bedrock para embeddings e LLM, e FAISS como mecanismo de busca vetorial.

- Linguagem: Python 3.11+
- Backend: Flask
- Frontend: HTML/CSS/JS estático
- Vetores: FAISS (persistido em S3)
- Embeddings: Amazon Titan Embeddings v2 (Bedrock)
- LLM: Anthropic Claude 3 Haiku (via Bedrock)
- Deploy alvo: EC2 (us-east-1)

---

## Arquitetura (visão geral)

Fluxo lógico do projeto:
- O usuário envia um PDF e uma pergunta para a aplicação (EC2).
- A EC2 faz o upload do PDF para um bucket S3 (armazenamento padrão).
- A EC2 usa o Amazon Bedrock (Titan Embeddings v2) para criar embeddings do conteúdo.
- O índice/embeddings são persistidos em um segundo bucket S3 (vetores/FAISS).
- O modelo em Bedrock (Claude) consulta os vetores para busca semântica e responde.
- A resposta é retornada pela EC2 ao usuário.

Diagrama (Mermaid):
```mermaid
flowchart LR
  subgraph AWS["AWS - us-east-1"]
    subgraph EC2SG["EC2 / App"]
      UQ[[Usuário INPUT]]
      EC2[EC2 API/App]
      UO[[Usuário OUTPUT]]
    end

    S3DOCS[(S3 - docs/PDF)]
    S3VEC[(S3 Vectors - vector-index)]

    subgraph BEDROCK["Bedrock"]
      TITAN[Titan Embeddings]
      KB[Knowledge Base / Retrieve]
      LLM[Modelo (ex: Claude em Bedrock)]
    end
  end

  %% fluxo
  UQ --> EC2
  EC2 -- "upload PDF" --> S3DOCS
  EC2 -- "chama Titan Embeddings" --> TITAN
  TITAN -- "retorna vetores" --> EC2
  EC2 -- "grava embeddings" --> S3VEC
  KB -- "retrieve" --- S3VEC
  EC2 -- "consulta + contexto da KB" --> LLM
  KB -- "chunks relevantes" --> LLM
  LLM -- "resposta" --> EC2
  EC2 --> UO
```

---

## Como funciona (passo a passo)

1) Sessão
- Ao acessar a aplicação, é criada uma session_id (cookie de sessão) e contadores internos (ex.: token_count).

2) Upload e indexação
- O PDF enviado é salvo no S3 de origem (S3_PDF_BUCKET).
- O backend baixa o PDF para um arquivo temporário, extrai o texto (PyPDFLoader), divide em chunks e gera embeddings com Titan v2 (Bedrock).
- Um índice FAISS é gerado e salvo no S3 de índices (S3_INDEX_BUCKET), separado por prefixo da sessão.
- O backend grava um status por sessão em s3://S3_INDEX_BUCKET/<session_id>/status.json (uploaded | ready | error).

3) Chat
- O frontend só habilita o chat quando o status estiver como ready.
- Para cada pergunta, o backend carrega o índice FAISS dessa sessão (do S3) e usa o LLM (Claude via Bedrock) com um prompt que restringe a resposta ao contexto recuperado.

4) Encerramento/limites
- Existe um limite de tokens por sessão (configurável via .env). Ao atingir, a sessão é reiniciada e os artefatos são limpos.

---

## Serviços AWS utilizados

- EC2: hospeda o backend Flask e os arquivos estáticos.
- S3 (bucket de documentos): persistência dos PDFs enviados.
- S3 (bucket de vetores): persistência do índice FAISS por sessão.
- Bedrock – Titan Embeddings v2: geração de embeddings dos chunks.
- Bedrock – Claude (Haiku, por padrão): modelo de linguagem para responder ao usuário. #Alterado para claude sonnet 3.7

---

## Endpoints

- GET / — interface web (upload + chat)
- POST /upload — recebe o PDF, processa e cria o índice da sessão
- resposta (sucesso): { ok: true, status: "ready", pdf_key, chunks }
- GET /status — retorna o status da sessão (uploaded | ready | error)
- POST /chat — recebe { question } e retorna { answer }
- GET /health — checagem com retorno mínimo { ok: true }

Observação:
- O front faz polling de /status para habilitar o chat apenas quando o índice estiver pronto.

---

## Variáveis de ambiente (.env)

Use o arquivo .env.example como base:
