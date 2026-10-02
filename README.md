# Sistema de Orçamentos Públicos

Sistema para auxiliar na obtenção de orçamentos de empresas fornecedoras, para embasar processos de compra pública (licitações, dispensas e adiantamentos).

---

## Como usar

### 1. Iniciar o sistema

Dê **duplo clique** no arquivo:
```
INICIAR_SERVIDOR.bat
```
O navegador abrirá automaticamente em **http://localhost:8000**

> **Na primeira vez:** o sistema baixará o catálogo CATMAT/CATSER do governo (~5 min). Nas próximas buscas será instantâneo.

---

### 2. Fluxo de uso

```
ETAPA 1 → Escolhe: Produto (CATMAT) ou Serviço (CATSER)
           + Digita o nome e descrição

ETAPA 2 → Sistema busca no catálogo do governo e apresenta
           até 10 opções ranqueadas por relevância
           → Você seleciona o código correto

ETAPA 3 → Informa: Estado + Cidade + Raio de busca (1–200 km)
           + Tipo de busca (Exata ou Por Similaridade)

ETAPA 4 → Lista de empresas fornecedoras com:
           - % de relevância (0–100%)
           - Endereço, telefone, site
           - Avaliação Google
           → Exporta em Excel ou PDF
           → Salva a busca para consulta futura
```

---

## Configuração das APIs

### Google Maps API Key (obrigatória para busca de empresas)

1. Acesse [console.cloud.google.com](https://console.cloud.google.com)
2. Crie um projeto e ative as APIs:
   - **Geocoding API**
   - **Places API**
3. Crie uma Chave de API em **Credenciais → Criar Credencial**
4. No sistema, clique em **⚙️ Configurações** e cole a chave

> O tier gratuito do Google oferece ~\$200/mês de crédito (~5.000 buscas grátis/mês)

### Gemini API Key (opcional — melhora o ranqueamento CATMAT/CATSER)

1. Acesse [aistudio.google.com](https://aistudio.google.com)
2. Gere uma chave gratuita
3. No sistema, clique em **⚙️ Configurações** e cole a chave no campo Gemini

> As chaves são salvas **apenas no seu navegador** (localStorage). Nunca são enviadas ao servidor.

---

## Estrutura do projeto

```
Orçamentos/
├── INICIAR_SERVIDOR.bat       ← Iniciar com duplo clique
├── .env                       ← Configurações do servidor
├── backend/
│   ├── main.py                ← Servidor FastAPI
│   ├── requirements.txt       ← Dependências Python
│   ├── database/
│   │   ├── models.py          ← Banco de dados SQLite
│   │   ├── orcamentos.db      ← Buscas salvas
│   │   └── catmat_cache.db    ← Cache CATMAT/CATSER
│   ├── routes/
│   │   ├── catmat.py          ← Busca CATMAT/CATSER
│   │   ├── empresas.py        ← Busca de empresas (Google Maps)
│   │   ├── buscas.py          ← Salvar/carregar buscas
│   │   └── export.py          ← Exportar Excel/PDF
│   └── services/
│       ├── governo_api.py     ← Integração API ComprasGov
│       ├── maps_api.py        ← Integração Google Maps/Places
│       ├── relevancia.py      ← Algoritmo de relevância
│       └── gemini_api.py      ← Integração Gemini (opcional)
└── frontend/
    ├── index.html             ← Interface web
    ├── css/style.css          ← Estilos
    └── js/app.js              ← Lógica frontend
```

---

## Endpoints da API

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/api/health` | Status do servidor |
| POST | `/api/catmat/buscar` | Busca CATMAT/CATSER |
| GET | `/api/catmat/status-cache` | Status do cache local |
| POST | `/api/catmat/recarregar-cache` | Recarrega cache do governo |
| POST | `/api/empresas/buscar` | Busca empresas no Google Maps |
| GET | `/api/buscas/` | Lista buscas salvas |
| POST | `/api/buscas/salvar` | Salva uma busca |
| POST | `/api/export/excel` | Exporta para Excel |
| POST | `/api/export/pdf` | Exporta para PDF |

---

## Tecnologias

- **Backend:** Python 3.14 + FastAPI + SQLite
- **Frontend:** HTML5 + CSS3 + JavaScript puro
- **APIs:** ComprasGov (CATMAT/CATSER) + Google Maps/Places + Gemini (opcional)
- **Exportação:** openpyxl (Excel) + reportlab (PDF)

---

## Iniciar manualmente (linha de comando)

```powershell
cd "C:\Users\matheus.ribeiro\Desktop\Matheus\5. IA\Orçamentos\backend"
C:\Users\matheus.ribeiro\AppData\Local\Programs\Python\Python314\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```
