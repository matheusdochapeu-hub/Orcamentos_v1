"""
Integração com a API de Dados Abertos do Compras.gov.br (CATMAT e CATSER).

Estratégia (melhorada):
  1. BUSCA ONLINE primeiro: consulta a API do governo passando o termo de busca
     nos parâmetros que ela aceita (descrição / PDM). O catálogo tem 350 mil+
     itens — baixar só as primeiras páginas (como antes) deixava a maioria dos
     itens inalcançável. A busca online resolve isso.
  2. FALLBACK em cache local: se a API estiver fora do ar, usa o cache SQLite
     (populado sob demanda) e aplica nosso filtro de similaridade.
  3. Em ambos os caminhos, ranqueamos por um score de relevância próprio.

Correções desta versão:
  - SQL parametrizado (sem interpolar texto do usuário na query → sem risco de
    quebra/injeção com aspas).
  - Score mais robusto (cobertura de palavras + bônus para match na frente +
    similaridade de sequência).
  - Busca online com múltiplos nomes de parâmetro (a API variou ao longo do
    tempo), tolerante a formatos de resposta.
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


from database.paths import db_path

BASE_API = "https://dadosabertos.compras.gov.br"
TIMEOUT  = 20.0
CACHE_DB = db_path("catmat_cache.db")

# Quantas páginas baixar para o cache de FALLBACK (100 itens/página × N páginas)
PAGINAS_CATMAT = 50
PAGINAS_CATSER = 30


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
    Score de relevância (0.0 a 1.0). Combina:
      - cobertura: proporção de palavras da query presentes no item (peso 65%)
      - frente:    bônus se o item COMEÇA com a 1ª palavra da query (peso 10%)
      - sequência: similaridade de sequência query × item (peso 25%)
    """
    if not palavras_query:
        return 0.0
    alvo = _norm(texto_item)
    hits = sum(1 for p in palavras_query if p in alvo)
    s_cov = hits / len(palavras_query)
    s_front = 1.0 if alvo.startswith(palavras_query[0]) else 0.0
    s_seq = lev_ratio(query_norm[:60], alvo[:60])
    return round(s_cov * 0.65 + s_front * 0.10 + s_seq * 0.25, 4)


def _montar_resultado_material(cod, nome_pdm, desc, score) -> dict:
    partes = [p.strip() for p in (desc or "").split(",")][:3]
    resumo = (nome_pdm or "").title()
    if partes and partes[0]:
        resumo += " | " + ", ".join(p.lower() for p in partes if p)
    return {
        "codigo": str(cod),
        "descricao": resumo or str(cod),
        "descricao_completa": desc or "",
        "tipo": "CATMAT",
        "score": score,
        "fonte": "ComprasGov/CATMAT",
    }


def _montar_resultado_servico(cod, nome_srv, nome_cls, nome_grp, score) -> dict:
    desc = " › ".join(p for p in [nome_srv, nome_cls, nome_grp] if p)
    return {
        "codigo": str(cod),
        "descricao": desc or str(cod),
        "descricao_completa": desc,
        "tipo": "CATSER",
        "score": score,
        "fonte": "ComprasGov/CATSER",
    }


# ─── BUSCA ONLINE na API do governo (preferencial) ─────────────────────────────

async def _buscar_online(tipo: str, texto_busca: str,
                         max_res: int = 10) -> list[dict]:
    """
    Consulta a API do ComprasGov passando o termo de busca. A API já passou por
    várias versões de nomes de parâmetro; tentamos os mais prováveis e paramos
    no primeiro que retornar itens. Em qualquer falha, devolve [] (o chamador
    então usa o cache como fallback).
    """
    query_norm = _norm(texto_busca)
    palavras_q = _palavras(texto_busca)
    if not palavras_q:
        return []

    termo = " ".join(palavras_q[:6])

    if tipo == "material":
        endpoint = f"{BASE_API}/modulo-material/4_consultarItemMaterial"
        # nomes de parâmetro candidatos para o filtro textual
        chaves_param = ["descricaoItem", "nomePdm", "descricao", "termo"]
    else:
        endpoint = f"{BASE_API}/modulo-servico/6_consultarItemServico"
        chaves_param = ["nomeServico", "descricao", "termo"]

    resultados: list[dict] = []
    async with httpx.AsyncClient(
        timeout=TIMEOUT, headers={"Accept": "application/json"}
    ) as client:
        for chave in chaves_param:
            try:
                r = await client.get(
                    endpoint,
                    params={chave: termo, "pagina": 1, "tamanhoPagina": 100},
                )
                if r.status_code != 200:
                    continue
                itens = r.json().get("resultado", [])
                if not itens:
                    continue

                for item in itens:
                    if tipo == "material":
                        cod = item.get("codigoItem", "")
                        nome_pdm = item.get("nomePdm", "") or ""
                        desc = item.get("descricaoItem", "") or ""
                        score = _calcular_score(query_norm, palavras_q,
                                                f"{nome_pdm} {desc}")
                        if score >= 0.05:
                            resultados.append(
                                _montar_resultado_material(cod, nome_pdm, desc, score)
                            )
                    else:
                        cod = item.get("codigoServico", "")
                        nome_srv = item.get("nomeServico", "") or ""
                        nome_cls = item.get("nomeClasse", "") or ""
                        nome_grp = item.get("nomeGrupo", "") or ""
                        score = _calcular_score(query_norm, palavras_q,
                                                f"{nome_srv} {nome_cls} {nome_grp}")
                        if score >= 0.05:
                            resultados.append(
                                _montar_resultado_servico(cod, nome_srv, nome_cls,
                                                          nome_grp, score)
                            )
                if resultados:
                    break  # parâmetro funcionou
            except Exception as e:
                print(f"[Online {tipo}/{chave}] {e}")
                continue

    resultados.sort(key=lambda x: x["score"], reverse=True)
    return resultados[:max_res]


# ─── Cache SQLite (fallback) ───────────────────────────────────────────────────

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
    return n >= 500


async def _popular_cache(tipo: str):
    """Baixa itens da API sem filtro e salva no SQLite (para fallback offline)."""
    print(f"[Cache] Populando {tipo.upper()}... (aguarde)")
    _init_cache()

    if tipo == "material":
        endpoint = f"{BASE_API}/modulo-material/4_consultarItemMaterial"
        n_paginas = PAGINAS_CATMAT
    else:
        endpoint = f"{BASE_API}/modulo-servico/6_consultarItemServico"
        n_paginas = PAGINAS_CATSER

    itens_mat, itens_ser = [], []
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
                            item.get("nomeClasse", "") or "",
                            item.get("nomeGrupo", "") or "",
                        ))
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"[Cache {tipo} p{pagina}] {e}")
                break

    con = sqlite3.connect(CACHE_DB)
    if itens_mat:
        con.executemany(
            "INSERT OR REPLACE INTO catmat (codigo, nome_pdm, descricao) VALUES (?,?,?)",
            itens_mat,
        )
    if itens_ser:
        con.executemany(
            "INSERT OR REPLACE INTO catser (codigo, nome_servico, nome_classe, nome_grupo) VALUES (?,?,?,?)",
            itens_ser,
        )
    con.commit()
    con.close()
    print(f"[Cache] {len(itens_mat or itens_ser)} itens salvos.")


def _buscar_no_cache(tipo: str, query: str, descricao: str,
                     max_res: int = 10) -> list[dict]:
    """Aplica nosso filtro de similaridade sobre o cache local (SQL parametrizado)."""
    texto_busca = f"{query} {descricao}".strip()
    query_norm = _norm(texto_busca)
    palavras_q = _palavras(texto_busca)
    if not palavras_q:
        return []

    con = sqlite3.connect(CACHE_DB)
    resultados: list[dict] = []
    termos = palavras_q[:4]
    like_params = [f"%{p}%" for p in termos]

    if tipo == "material":
        where = " OR ".join(
            ["LOWER(nome_pdm || ' ' || descricao) LIKE ?" for _ in termos]
        ) or "1=1"
        rows = con.execute(
            f"SELECT codigo, nome_pdm, descricao FROM catmat WHERE {where} LIMIT 500",
            like_params,
        ).fetchall()
        for cod, nome_pdm, desc in rows:
            score = _calcular_score(query_norm, palavras_q, f"{nome_pdm} {desc}")
            if score >= 0.05:
                resultados.append(_montar_resultado_material(cod, nome_pdm, desc, score))
    else:
        where = " OR ".join(
            ["LOWER(nome_servico || ' ' || nome_classe || ' ' || nome_grupo) LIKE ?"
             for _ in termos]
        ) or "1=1"
        rows = con.execute(
            f"SELECT codigo, nome_servico, nome_classe, nome_grupo FROM catser WHERE {where} LIMIT 500",
            like_params,
        ).fetchall()
        for cod, nome_srv, nome_cls, nome_grp in rows:
            score = _calcular_score(query_norm, palavras_q,
                                    f"{nome_srv} {nome_cls} {nome_grp}")
            if score >= 0.05:
                resultados.append(
                    _montar_resultado_servico(cod, nome_srv, nome_cls, nome_grp, score)
                )

    con.close()
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
      1. Tenta busca ONLINE na API do governo (cobre o catálogo inteiro).
      2. Se vier vazio/offline, usa o cache local como FALLBACK (populando-o
         na primeira vez).
      3. Retorna os melhores resultados ordenados por relevância.
    """
    texto_busca = f"{query} {descricao}".strip()

    # 1) Busca online (preferencial)
    resultados = await _buscar_online(tipo, texto_busca, max_res=max_resultados)

    # 2) Fallback no cache local
    if not resultados:
        if not _cache_populado(tipo):
            await _popular_cache(tipo)
        resultados = _buscar_no_cache(tipo, query, descricao, max_res=max_resultados)

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
