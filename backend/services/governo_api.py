"""
Integração com a API de Dados Abertos do Compras.gov.br (CATMAT e CATSER).

Estratégia simples e eficaz:
  1. Busca itens da API SEM filtro de texto (a API não filtra bem por nome)
  2. Aplica NOSSO PRÓPRIO filtro de similaridade nos resultados
  3. Retorna os melhores matches ranqueados por relevância

Cache local em SQLite para não bater na API toda vez.
"""
import httpx
import asyncio
import re
import sqlite3
from pathlib import Path
from unidecode import unidecode
from difflib import SequenceMatcher


def lev_ratio(s1: str, s2: str) -> float:
    return SequenceMatcher(None, s1 or "", s2 or "").ratio()


BASE_API = "https://dadosabertos.compras.gov.br"
TIMEOUT  = 20.0
CACHE_DB = Path(__file__).parent.parent / "database" / "catmat_cache.db"

# Quantas páginas baixar para o cache (100 itens/página × N páginas)
PAGINAS_CATMAT = 50   # ~5.000 itens (cobre a maioria dos materiais comuns)
PAGINAS_CATSER = 30   # ~3.000 itens


# ─── Utilidades ───────────────────────────────────────────────────────────────

def _norm(texto: str) -> str:
    """Normaliza: sem acentos, só letras/números/espaços, minúsculas."""
    t = unidecode(texto or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _palavras(texto: str) -> list[str]:
    """Extrai palavras relevantes (sem stopwords, mín. 3 chars)."""
    stop = {"de","da","do","dos","das","para","com","por","em",
            "um","uma","nos","nas","que","ao","os","as","e","a","o","se","na","no"}
    return [p for p in _norm(texto).split() if len(p) >= 3 and p not in stop]


def _calcular_score(query_norm: str, palavras_query: list[str],
                    texto_item: str) -> float:
    """
    Nosso filtro de relevância (0.0 a 1.0).
    Combina:
      - Proporção de palavras da query encontradas no item (peso 75%)
      - Similaridade Levenshtein entre query e início do texto (peso 25%)
    """
    if not palavras_query:
        return 0.0
    alvo  = _norm(texto_item)
    hits  = sum(1 for p in palavras_query if p in alvo)
    s_kw  = hits / len(palavras_query)
    s_lev = lev_ratio(query_norm[:60], alvo[:60])
    return round(s_kw * 0.75 + s_lev * 0.25, 4)


# ─── Cache SQLite ──────────────────────────────────────────────────────────────

def _init_cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CACHE_DB)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS catmat (
            codigo   TEXT PRIMARY KEY,
            nome_pdm TEXT,
            descricao TEXT
        );
        CREATE TABLE IF NOT EXISTS catser (
            codigo        TEXT PRIMARY KEY,
            nome_servico  TEXT,
            nome_classe   TEXT,
            nome_grupo    TEXT
        );
        CREATE TABLE IF NOT EXISTS cache_status (
            chave TEXT PRIMARY KEY,
            valor TEXT
        );
    """)
    con.commit()
    con.close()


def _cache_populado(tipo: str) -> bool:
    _init_cache()
    tabela = "catmat" if tipo == "material" else "catser"
    con = sqlite3.connect(CACHE_DB)
    n = con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
    con.close()
    return n >= 500   # considera populado se tiver pelo menos 500 itens


async def _popular_cache(tipo: str):
    """Baixa itens da API sem filtro e salva no SQLite."""
    print(f"[Cache] Populando {tipo.upper()}... (aguarde ~30s)")
    _init_cache()

    if tipo == "material":
        endpoint = f"{BASE_API}/modulo-material/4_consultarItemMaterial"
        n_paginas = PAGINAS_CATMAT
    else:
        endpoint = f"{BASE_API}/modulo-servico/6_consultarItemServico"
        n_paginas = PAGINAS_CATSER

    itens_mat  = []
    itens_ser  = []

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for pagina in range(1, n_paginas + 1):
            try:
                r = await client.get(endpoint, params={"pagina": pagina, "tamanhoPagina": 100})
                if r.status_code != 200:
                    break
                resultado = r.json().get("resultado", [])
                if not resultado:
                    break

                if tipo == "material":
                    for item in resultado:
                        itens_mat.append((
                            str(item.get("codigoItem", "")),
                            item.get("nomePdm", "") or "",
                            item.get("descricaoItem", "") or "",
                        ))
                else:
                    for item in resultado:
                        itens_ser.append((
                            str(item.get("codigoServico", "")),
                            item.get("nomeServico", "") or "",
                            item.get("nomeClasse",  "") or "",
                            item.get("nomeGrupo",   "") or "",
                        ))

                await asyncio.sleep(0.3)   # respeita rate limit

            except Exception as e:
                print(f"[Cache {tipo} p{pagina}] {e}")
                break

    con = sqlite3.connect(CACHE_DB)
    if itens_mat:
        con.executemany(
            "INSERT OR REPLACE INTO catmat (codigo, nome_pdm, descricao) VALUES (?,?,?)",
            itens_mat
        )
    if itens_ser:
        con.executemany(
            "INSERT OR REPLACE INTO catser (codigo, nome_servico, nome_classe, nome_grupo) VALUES (?,?,?,?)",
            itens_ser
        )
    con.commit()
    con.close()
    print(f"[Cache] {len(itens_mat or itens_ser)} itens salvos.")


# ─── Busca com NOSSO filtro ────────────────────────────────────────────────────

def _buscar_com_filtro_proprio(tipo: str, query: str, descricao: str,
                               max_res: int = 10) -> list[dict]:
    """
    Aplica NOSSO filtro de similaridade sobre o cache local.
    Retorna os itens mais relevantes ordenados por score.
    """
    texto_busca  = f"{query} {descricao}".strip()
    query_norm   = _norm(texto_busca)
    palavras_q   = _palavras(texto_busca)
    cat          = "CATMAT" if tipo == "material" else "CATSER"

    con = sqlite3.connect(CACHE_DB)
    resultados: list[dict] = []

    if tipo == "material":
        # Pré-filtro SQL: traz apenas itens que contenham ao menos 1 palavra da query
        # Isso evita varrer todos os 2.000 registros com Python
        where_parts = " OR ".join(
            [f"UPPER(nome_pdm || ' ' || descricao) LIKE '%{p.upper()}%'" for p in palavras_q[:4]]
        ) if palavras_q else "1=1"

        rows = con.execute(
            f"SELECT codigo, nome_pdm, descricao FROM catmat WHERE {where_parts} LIMIT 500"
        ).fetchall()

        for cod, nome_pdm, desc in rows:
            texto_item = f"{nome_pdm} {desc}"
            score = _calcular_score(query_norm, palavras_q, texto_item)
            if score < 0.05:
                continue
            # Descrição curta: PDM + primeiras 3 características
            partes = [p.strip() for p in (desc or "").split(",")][:3]
            resumo = (nome_pdm or "").title()
            if partes:
                resumo += " | " + ", ".join(p.lower() for p in partes)
            resultados.append({
                "codigo":   cod,
                "descricao": resumo,
                "descricao_completa": desc,
                "tipo":     cat,
                "score":    score,
                "fonte":    "ComprasGov/CATMAT",
            })

    else:  # CATSER
        where_parts = " OR ".join(
            [f"UPPER(nome_servico || ' ' || nome_classe || ' ' || nome_grupo) LIKE '%{p.upper()}%'"
             for p in palavras_q[:4]]
        ) if palavras_q else "1=1"

        rows = con.execute(
            f"SELECT codigo, nome_servico, nome_classe, nome_grupo FROM catser WHERE {where_parts} LIMIT 500"
        ).fetchall()

        for cod, nome_srv, nome_cls, nome_grp in rows:
            texto_item = f"{nome_srv} {nome_cls} {nome_grp}"
            score = _calcular_score(query_norm, palavras_q, texto_item)
            if score < 0.05:
                continue
            desc = " › ".join(p for p in [nome_srv, nome_cls, nome_grp] if p)
            resultados.append({
                "codigo":   cod,
                "descricao": desc,
                "descricao_completa": desc,
                "tipo":     cat,
                "score":    score,
                "fonte":    "ComprasGov/CATSER",
            })

    con.close()

    # Ordena pelo nosso score e retorna os melhores
    resultados.sort(key=lambda x: x["score"], reverse=True)
    return resultados[:max_res]


# ─── FUNÇÃO PRINCIPAL ─────────────────────────────────────────────────────────

async def buscar_catmat_catser(
    tipo: str,
    query: str,
    descricao: str = "",
    max_resultados: int = 10,
) -> list[dict]:
    """
    Busca CATMAT (tipo='material') ou CATSER (tipo='servico').

    Fluxo:
      1. Se o cache local estiver vazio → baixa itens da API (sem filtro)
      2. Aplica nosso filtro de similaridade no cache local
      3. Retorna os melhores resultados ordenados por relevância
    """
    # Popula o cache se necessário (primeira execução)
    if not _cache_populado(tipo):
        await _popular_cache(tipo)

    # Aplica NOSSO filtro sobre os dados cacheados
    resultados = _buscar_com_filtro_proprio(tipo, query, descricao, max_res=max_resultados)

    if not resultados:
        cat = "CATMAT" if tipo == "material" else "CATSER"
        return [{
            "codigo": "—",
            "descricao": (
                f"Nenhum resultado para '{query}'. "
                f"Tente termos mais simples ou consulte: "
                f"https://catalogo.compras.gov.br/cnbs-web/busca"
            ),
            "descricao_completa": "",
            "tipo": cat,
            "score": 0.0,
            "fonte": "—",
        }]

    return resultados
