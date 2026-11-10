# AGENTS.md — Sistema de Orçamentos Públicos

> Contexto para agentes de IA. Escopo: **esta pasta**.

## O que é

Ferramenta de apoio a compras públicas: busca o item no catálogo **CATMAT/CATSER** do
governo, localiza empresas fornecedoras por CNAE/município/UF (Receita, PNCP, OSM,
Geoapify, Google Maps opcional) e exporta a lista de fornecedores em Excel ou PDF para
instruir processos de dispensa, licitação ou adiantamento.

## Stack

- **Backend:** Python + FastAPI + SQLite (`aiosqlite`), organizado em
  `backend/{database,routes,services}`.
- **Frontend:** HTML/CSS/JS estático (PWA) em `frontend/`, servido pelo próprio FastAPI.
- Sem build e sem Node.

## Rodar localmente

```powershell
.\INICIAR_SERVIDOR.bat      # atalho: sobe o servidor e abre o navegador
# equivalente:
pip install -r requirements.txt
cd backend; python -m uvicorn main:app --host 0.0.0.0 --port 8000
```
A **primeira** busca baixa o catálogo CATMAT/CATSER do governo (~5 min); depois é instantâneo.

## Testes e verificação

```powershell
pip install -r requirements-dev.txt
python -m compileall -q backend      # sintaxe de todo o Python
python -m pytest tests -q            # 8 testes de fumaça da API
```

## Deploy

- **Backend:** Render, via `render.yaml` (`cd backend && uvicorn main:app`).
  `Dockerfile` + `fly.toml` também existem como alternativas.
- **Frontend/GitHub Pages:** `.github/workflows/deploy-pages.yml` publica `frontend/`.
- **CI:** `.github/workflows/ci.yml` roda `compileall` + pytest em todo push na `master`.
- Em produção, `DATA_DIR` deve apontar para o volume persistente (ex.: `/data`),
  senão os SQLite são perdidos a cada deploy.

## Arquivos-chave

| Arquivo | Papel |
|---|---|
| `backend/main.py` | App FastAPI, CORS, headers de embed, montagem do frontend |
| `backend/database/paths.py` | Resolve `DATA_DIR` dos bancos SQLite |
| `backend/routes/` | Endpoints por domínio (catmat, empresas, buscas, export, localidades) |
| `backend/services/empresas/` | Fontes de fornecedores (Receita, PNCP, OSM, Geoapify, Maps) |
| `frontend/` | PWA servida em `/` |
| `docs/` | **Legado** do Pages antigo (duplicava `frontend/`) — não é a fonte |
| `.env.example` | Documenta todas as variáveis; o `.env` real nunca vai para o Git |

## Regras críticas

1. **Nunca versionar o `.env`.** Ele já foi rastreado no passado (só com placeholders) e
   foi removido do índice em 10/11/2026. O `.gitignore` cobre `.env`; não force.
2. **Chaves de API ficam no servidor.** Geoapify e Google Maps são usados pelo backend;
   não expor chave no JavaScript do frontend.
3. **Toda fonte externa precisa degradar bem:** se Receita, PNCP, OSM ou Google estiverem
   fora do ar, a busca deve continuar funcionando com as fontes disponíveis.
4. **Não quebrar o embed:** o app roda em iframe no Portal Gestor Público. Mexer em
   `EMBED_ANCESTORS` ou remover o middleware de CSP quebra o embed.
5. **Nunca commitar os `.db`** (`catmat_cache.db`, `orcamentos.db`) nem pastas de dados.
6. A coleta do CATMAT é pesada e cara: não disparar em teste nem em CI.

## Vizinhança (ecossistema)

- Projeto de **trabalho**, separado dos apps pessoais (Matrix, Maracaju, Faturas, FisioFlow).
- Não compartilha dados com nenhum outro app da pasta.
