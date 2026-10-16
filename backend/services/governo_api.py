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

# Limiar mínimo de relevância para um item entrar nos resultados. Baixo de
# propósito (busca abrangente/tolerante) — o ranqueamento ordena os melhores.
_LIMIAR = 0.12


# ─── Utilidades ───────────────────────────────────────────────────────────────

def _norm(texto: str) -> str:
    """Normaliza: sem acentos, só letras/números/espaços, minúsculas."""
    t = unidecode(texto or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Sinônimos / termos equivalentes comuns em compras públicas. Cada palavra da
# query é EXPANDIDA para incluir seus sinônimos, aumentando a chance de casar
# com a descrição oficial do catálogo (que usa termos técnicos).
_SINONIMOS = {
    "computador": ["microcomputador", "cpu", "desktop", "pc"],
    "notebook": ["laptop", "portatil", "notebook"],
    "impressora": ["impressora", "multifuncional", "jato", "laser"],
    "cartucho": ["cartucho", "toner", "tinta"],
    "caneta": ["caneta", "esferografica"],
    "papel": ["papel", "sulfite", "resma", "a4"],
    "armario": ["armario", "roupeiro"],
    "cadeira": ["cadeira", "poltrona", "assento"],
    "mesa": ["mesa", "escrivaninha", "bancada"],
    "estofado": ["estofado", "sofa", "poltrona"],
    "geladeira": ["geladeira", "refrigerador"],
    "ar": ["ar", "condicionado", "climatizador", "split"],
    "ventilador": ["ventilador", "circulador"],
    "veiculo": ["veiculo", "automovel", "carro", "viatura"],
    "pneu": ["pneu", "pneumatico"],
    "combustivel": ["combustivel", "gasolina", "diesel", "etanol", "alcool"],
    "remedio": ["medicamento", "farmaco", "remedio"],
    "medicamento": ["medicamento", "farmaco", "remedio"],
    "luva": ["luva", "procedimento", "cirurgica"],
    "mascara": ["mascara", "respiratoria", "cirurgica"],
    "limpeza": ["limpeza", "higiene", "detergente", "desinfetante", "saneante"],
    "uniforme": ["uniforme", "vestuario", "fardamento"],
    "notebook ": ["laptop"],
    "lampada": ["lampada", "led", "luminaria"],
    "merenda": ["merenda", "alimento", "alimenticio", "genero"],
    "agua": ["agua", "mineral", "potavel"],
    "livro": ["livro", "didatico", "literario", "obra"],
}


def _palavras(texto: str, expandir: bool = False) -> list[str]:
    """
    Extrai palavras relevantes (sem stopwords, mín. 3 chars).
    Se expandir=True, acrescenta sinônimos de cada palavra (para a busca ficar
    mais abrangente — tolera o usuário usar termo coloquial em vez do técnico).
    """
    stop = {"de","da","do","dos","das","para","com","por","em",
            "um","uma","nos","nas","que","ao","os","as","e","a","o","se","na","no",
            "tipo","modelo","marca","cor","tamanho"}
    base = [p for p in _norm(texto).split() if len(p) >= 3 and p not in stop]
    if not expandir:
        return base
    expandido = list(base)
    for p in base:
        for syn in _SINONIMOS.get(p, []):
            if syn not in expandido:
                expandido.append(syn)
    return expandido


def _match_palavra(palavra: str, tokens_item: list[str], texto_item: str) -> float:
    """
    Mede o quão bem UMA palavra da query casa com o item (0.0 a 1.0),
    de forma TOLERANTE a erros de digitação/plural:
      1.0  → palavra aparece literal no texto (substring)
      0.9  → casa por radical com algum token (prefixo de 4+ letras)
      0..1 → melhor similaridade fuzzy (Levenshtein) com algum token do item,
             contando só se ficar razoavelmente parecida (>= 0.78)
    """
    if palavra in texto_item:
        return 1.0
    melhor = 0.0
    radical = palavra[:4]
    for tok in tokens_item:
        if len(palavra) >= 4 and len(tok) >= 4 and (tok.startswith(radical) or palavra.startswith(tok[:4])):
            melhor = max(melhor, 0.9)
            continue
        r = lev_ratio(palavra, tok)
        if r >= 0.78:                      # tolera ~1-2 letras trocadas
            melhor = max(melhor, r)
    return melhor


def _calcular_score(query_norm: str, palavras_query: list[str],
                    texto_item: str) -> float:
    """
    Score de relevância (0.0 a 1.0), TOLERANTE a erros. Combina:
      - cobertura: soma dos matches (fuzzy) das palavras da query / nº palavras (70%)
      - frente:    bônus se o item COMEÇA com a 1ª palavra da query (10%)
      - sequência: similaridade de sequência query × item (20%)
    """
    if not palavras_query:
        return 0.0
    alvo = _norm(texto_item)
    tokens_item = alvo.split()
    soma = sum(_match_palavra(p, tokens_item, alvo) for p in palavras_query)
    s_cov = soma / len(palavras_query)
    s_front = 1.0 if alvo.startswith(palavras_query[0]) else 0.0
    s_seq = lev_ratio(query_norm[:60], alvo[:60])
    return round(s_cov * 0.70 + s_front * 0.10 + s_seq * 0.20, 4)


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
    palavras_q = _palavras(texto_busca, expandir=True)   # com sinônimos
    palavras_base = _palavras(texto_busca)               # sem sinônimos (p/ termo da API)
    if not palavras_q:
        return []

    # Termo enviado à API: usa as palavras originais (sem sinônimos, p/ não
    # confundir o filtro do governo). O ranqueamento local é que usa sinônimos.
    termo = " ".join(palavras_base[:6])

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
                        if score >= _LIMIAR:
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
                        if score >= _LIMIAR:
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
    palavras_q = _palavras(texto_busca, expandir=True)   # ranqueamento c/ sinônimos
    if not palavras_q:
        return []

    con = sqlite3.connect(CACHE_DB)
    resultados: list[dict] = []
    # Pré-filtro SQL amplo: usa as palavras E seus RADICAIS (4 letras) e
    # sinônimos, com LIKE, só para reduzir o conjunto; o recorte fino é no score.
    radicais = []
    for p in palavras_q[:8]:
        radicais.append(p)
        if len(p) >= 5:
            radicais.append(p[:4])   # pega plural/variação (ex.: "cadeir" de "cadeiras")
    # dedup preservando ordem
    vistos = set(); termos = []
    for t in radicais:
        if t not in vistos:
            vistos.add(t); termos.append(t)
    termos = termos[:10]
    like_params = [f"%{t}%" for t in termos]

    if tipo == "material":
        where = " OR ".join(
            ["LOWER(nome_pdm || ' ' || descricao) LIKE ?" for _ in termos]
        ) or "1=1"
        rows = con.execute(
            f"SELECT codigo, nome_pdm, descricao FROM catmat WHERE {where} LIMIT 1500",
            like_params,
        ).fetchall()
        for cod, nome_pdm, desc in rows:
            score = _calcular_score(query_norm, palavras_q, f"{nome_pdm} {desc}")
            if score >= _LIMIAR:
                resultados.append(_montar_resultado_material(cod, nome_pdm, desc, score))
    else:
        where = " OR ".join(
            ["LOWER(nome_servico || ' ' || nome_classe || ' ' || nome_grupo) LIKE ?"
             for _ in termos]
        ) or "1=1"
        rows = con.execute(
            f"SELECT codigo, nome_servico, nome_classe, nome_grupo FROM catser WHERE {where} LIMIT 1500",
            like_params,
        ).fetchall()
        for cod, nome_srv, nome_cls, nome_grp in rows:
            score = _calcular_score(query_norm, palavras_q,
                                    f"{nome_srv} {nome_cls} {nome_grp}")
            if score >= _LIMIAR:
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
