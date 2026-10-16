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
           + Fonte dos dados (Receita Federal / Google Maps / Ambas)
           + Tipo de busca (Exata ou Por Similaridade)

ETAPA 4 → Lista de empresas fornecedoras com:
           - % de relevância (0–100%)
           - CNPJ, CNAE e situação cadastral (fonte Receita)
           - Endereço, telefone, site
           - Avaliação Google (quando a fonte for o Google)
           → Exporta em Excel ou PDF
           → Salva a busca para consulta futura
```

---

## Fontes de busca de empresas

O sistema busca fornecedores **automaticamente em todas as fontes disponíveis**
e combina os resultados (deduplicando por CNPJ) — o usuário não precisa escolher
nada. As fontes são:

| Fonte | O que faz | Precisa de chave? |
|-------|-----------|-------------------|
| ✅ **PNCP** (alta precisão) | Encontra empresas que **já forneceram este tipo de item ao poder público** (histórico de contratações). Comprovadamente vendem o produto → vão para o **topo** da lista com o selo "já forneceu". | **Não** |
| 🏛️ **Receita Federal** (cobertura) | Lista empresas **ativas** da base CNPJ filtrando por **CNAE (atividade) + município + UF**. Completa a lista quando o histórico é escasso. | **Não** |
| 🗺️ **Google Maps** (usada se houver chave) | Enriquece os resultados com estabelecimentos do Google (avaliação, telefone). | Sim (opcional) |

> **Por que a busca ficou mais precisa:** o CNAE diz só a *atividade declarada*
> da empresa (genérico). O **PNCP** mostra quem **realmente já vendeu** aquele
> item em compras públicas — evidência concreta de que fornece o produto. Essas
> empresas recebem um piso alto de relevância e o selo ✓. A Receita entra como
> complemento de cobertura. Há um filtro **"Só quem já forneceu"** nos resultados.

### Cruzamentos adicionais de dados

- **OpenStreetMap (mapa) — grátis, sem chave:** cruza os resultados com o
  **OpenStreetMap** via a Overpass API (pública, sem chave, sem cartão). Faz
  duas coisas: **(1) descoberta** — traz estabelecimentos do mapa que casam com
  o produto na região (marcados com 🗺️ "do mapa"), com telefone/site/horário; e
  **(2) enriquecimento** — completa telefone/site/horário das empresas da
  Receita/PNCP que estavam sem contato, cruzando por nome + proximidade
  (marcado com 🗺️ "contato do mapa"). Ligado por padrão; `OSM_ENABLED=0` desliga.
- **CNAEs secundários:** além da atividade principal, o sistema cruza os
  **CNAEs secundários** da empresa com o item buscado — uma empresa de "comércio
  geral" com secundário de "móveis/estofados" sobe na relevância.
- **Mais contatos:** quando disponíveis, mostra **2º telefone** e **e-mail**
  (além do telefone principal em destaque).
- **Empresas sancionadas (CEIS/CNEP) — opcional:** se você configurar uma chave
  gratuita do Portal da Transparência (`PORTAL_TRANSPARENCIA_KEY`), as empresas
  **impedidas de contratar** com a administração pública recebem um alerta 🚫 e
  podem ser ocultadas com um filtro. Sem a chave, esse cruzamento é ignorado.

> A seleção de fonte **não aparece na tela**. Se uma chave do Google estiver
> configurada, ela entra automaticamente na busca; caso contrário, o sistema
> usa apenas a Receita, de forma transparente.

Como o item CATMAT/CATSER escolhido vira uma busca de empresas: o sistema
mapeia o item para um conjunto de **CNAEs** compatíveis
(`backend/services/cnae_map.py`) e usa esses códigos para filtrar a base CNPJ.
O mapa é facilmente expansível — basta adicionar palavras-chave → CNAEs.

> A fonte Receita usa o **MinhaReceita** (projeto open-source que expõe a base
> de dados abertos da Receita Federal com busca por **CNAE + município + UF**).
> Por padrão aponta para a instância pública `minhareceita.org`. Você pode
> **auto-hospedar** sua própria instância e apontar `RECEITA_CUSTOM_URL` para ela
> no `.env` — recomendado para uso intenso e sem limites de terceiros.
>
> Se a consulta à base falhar (URL errada, instância fora do ar, limite de
> requisições), o sistema **mostra o motivo na tela** em vez de simplesmente
> retornar "0 empresas".
>
> O MinhaReceita filtra o município pelo **código IBGE** (não pelo nome). O app
> resolve isso automaticamente via a API pública de Localidades do IBGE
> (`servicodados.ibge.gov.br`), convertendo "cidade + UF" no código correto.
> Por isso **selecionar o Estado (UF)** torna a busca mais precisa.

### Raio real em km (também sem Google)

A base da Receita entrega empresas por **município/UF**. Para respeitar o
**raio em km**, o sistema geocodifica (via **OpenStreetMap/Nominatim**, grátis e
sem chave, com cache local) o centro da busca e o município de cada empresa,
calcula a distância real (Haversine) e descarta quem está fora do raio. Se a
geocodificação falhar, a busca continua por município (sem o filtro fino),
em vez de quebrar. A distância aparece em cada card de empresa.

---

## Configuração das APIs

### Google Maps API Key (OPCIONAL)

A busca **funciona sem o Google** usando a fonte Receita Federal. A chave só é
necessária se você escolher a fonte **🗺️ Google Maps** ou **🔀 Ambas**.

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
│       ├── governo_api.py     ← Busca CATMAT/CATSER (online + cache fallback)
│       ├── cnae_map.py        ← Mapeia item → CNAEs (p/ busca na Receita)
│       ├── geocode.py         ← Geocodificação OSM/Nominatim + raio real (km)
│       ├── maps_api.py        ← Integração Google Maps/Places (baixo nível)
│       ├── relevancia.py      ← Algoritmo de relevância (adapta por fonte)
│       ├── gemini_api.py      ← Integração Gemini (opcional)
│       └── empresas/          ← Provedores de busca de empresas
│           ├── base.py        ← Contrato comum + schema canônico
│           ├── receita_cnpj.py← Fonte Receita Federal (CNAE+município+UF)
│           ├── google_maps.py ← Fonte Google Maps (opcional)
│           └── agregador.py   ← Combina fontes e deduplica
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
