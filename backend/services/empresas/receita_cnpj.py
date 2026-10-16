"""
Provedor de empresas baseado na BASE CNPJ DA RECEITA FEDERAL via MinhaReceita.

Esta é a fonte principal de fornecedores (alternativa ao Google Maps): encontra
empresas reais filtrando por CNAE (atividade econômica) + município + UF.

Por que MinhaReceita (projeto open-source minhareceita.org):
  * Expõe a base de dados abertos da Receita Federal com busca FILTRADA por
    CNAE + UF + município — exatamente o que precisamos.
  * Pode ser AUTO-HOSPEDADO (self-host) — basta subir a instância e apontar
    RECEITA_CUSTOM_URL para ela. Enquanto isso, usa a instância pública.
  * Gratuito, sem chave.

Configuração (.env):
  RECEITA_BACKEND=minhareceita            # padrão
  RECEITA_CUSTOM_URL=https://minhareceita.org   # troque para seu self-host

DIAGNÓSTICO: ao contrário da versão anterior (que engolia erros e devolvia lista
vazia), aqui qualquer falha da fonte vira uma EXCEÇÃO com mensagem clara, que o
backend repassa ao frontend. Assim, "0 empresas" por erro de configuração/rede
fica distinguível de "0 empresas" por não haver fornecedores na região.

IMPORTANTE: formatos de resposta variam entre versões/instâncias. A
normalização (`_normalizar_item`) e a extração de lista (`_extrair_lista`) são
defensivas e cobrem as chaves mais comuns.
"""
from __future__ import annotations

import os
import json
import asyncio
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import httpx

from services.empresas.base import ProvedorEmpresas, empresa_vazia
from services.cnae_map import mapear_cnaes, formatar_cnae
from services import geocode
from services import ibge


def _cnae_numerico(cnae: str) -> str:
    """'4754-7/01' ou '4754701' → '4754701' (só dígitos, como o MinhaReceita espera)."""
    return "".join(ch for ch in str(cnae) if ch.isdigit())

TIMEOUT = 30.0
CACHE_DB = Path(__file__).resolve().parent.parent.parent / "database" / "catmat_cache.db"

# URL base da instância MinhaReceita (pública por padrão; troque p/ self-host).
# Aceita tanto RECEITA_CUSTOM_URL (nome genérico) quanto RECEITA_MINHARECEITA_URL.
MINHARECEITA_URL = (
    os.getenv("RECEITA_MINHARECEITA_URL")
    or os.getenv("RECEITA_CUSTOM_URL")
    or "https://minhareceita.org"
).rstrip("/")


class ReceitaIndisponivel(Exception):
    """Erro de configuração/rede/formato da fonte Receita (para exibir ao usuário)."""


# ─── Cache de resultados (reaproveita o SQLite já existente) ───────────────────

def _init_cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CACHE_DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS cache_empresas (
               chave     TEXT PRIMARY KEY,
               dados     TEXT NOT NULL,
               expira_em TEXT NOT NULL
           )"""
    )
    con.commit()
    con.close()


def _cache_get(chave: str) -> list[dict] | None:
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        row = con.execute(
            "SELECT dados, expira_em FROM cache_empresas WHERE chave = ?", (chave,)
        ).fetchone()
        con.close()
        if not row:
            return None
        dados, expira = row
        if datetime.fromisoformat(expira) < datetime.now():
            return None
        return json.loads(dados)
    except Exception:
        return None


def _cache_set(chave: str, dados: list[dict], horas: int = 24):
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        con.execute(
            "INSERT OR REPLACE INTO cache_empresas (chave, dados, expira_em) VALUES (?,?,?)",
            (chave, json.dumps(dados, ensure_ascii=False),
             (datetime.now() + timedelta(hours=horas)).isoformat()),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def _chave(cnaes: list[str], uf: str, municipio: str) -> str:
    raw = json.dumps(
        {"src": "minhareceita", "c": sorted(cnaes), "uf": uf, "m": municipio},
        sort_keys=True,
    )
    return "emp_" + hashlib.md5(raw.encode()).hexdigest()


# ─── Normalização defensiva ────────────────────────────────────────────────────

def _primeiro(d: dict, *chaves, default=""):
    """Retorna o primeiro valor não-vazio entre várias chaves possíveis."""
    for k in chaves:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _montar_telefone(item: dict) -> str:
    # MinhaReceita costuma trazer ddd_telefone_1 / ddd_telefone_2
    tel = _primeiro(item, "telefone", "telefone1", "phone", "ddd_telefone_1", default="")
    return str(tel).strip()


def _normalizar_item(item: dict, municipio_fallback="", uf_fallback="") -> dict:
    """Converte um registro de empresa (formato variado) no schema canônico."""
    emp = empresa_vazia()
    emp["fonte"] = "receita"

    cnpj = str(_primeiro(item, "cnpj", "cnpj_completo", "cnpjCompleto", default="")).strip()
    emp["cnpj"] = cnpj
    emp["id"] = cnpj or _primeiro(item, "id", default="")

    emp["razao_social"] = _primeiro(item, "razao_social", "razaoSocial", "nome", "company", default="")
    emp["nome_fantasia"] = _primeiro(item, "nome_fantasia", "nomeFantasia", "fantasia",
                                     "trade_name", default="")
    emp["nome"] = emp["nome_fantasia"] or emp["razao_social"]

    emp["municipio"] = _primeiro(item, "municipio", "cidade", "city", default="") or municipio_fallback
    emp["uf"] = _primeiro(item, "uf", "estado", "state", default="") or uf_fallback
    emp["cep"] = _primeiro(item, "cep", "zip", default="")

    endereco = _primeiro(item, "endereco", "logradouro_completo", "address", default="")
    if not endereco:
        partes = [
            _primeiro(item, "descricao_tipo_de_logradouro", "tipo_logradouro", default=""),
            _primeiro(item, "logradouro", "street", default=""),
            str(_primeiro(item, "numero", "number", default="")),
            _primeiro(item, "bairro", "district", default=""),
        ]
        endereco = " ".join(str(p) for p in partes if p).strip()
    cidade_uf = ", ".join(p for p in [emp["municipio"], emp["uf"]] if p)
    emp["endereco"] = ", ".join(p for p in [endereco, cidade_uf] if p)

    emp["telefone"] = _montar_telefone(item)
    emp["telefone2"] = str(_primeiro(item, "telefone2", "ddd_telefone_2", default="")).strip()
    emp["email"] = _primeiro(item, "email", "correio_eletronico", default="")
    emp["situacao"] = str(_primeiro(item, "descricao_situacao_cadastral",
                                    "situacao_cadastral", "situacao", "status",
                                    default="")).upper()

    cnae = str(_primeiro(item, "cnae_fiscal", "cnae_fiscal_principal", "cnae_principal",
                         "cnae", "primary_activity_code", default=""))
    emp["cnae_principal"] = formatar_cnae(cnae)
    emp["cnae_descricao"] = _primeiro(item, "cnae_fiscal_descricao", "cnae_descricao",
                                      "primary_activity", default="")

    # CNAEs secundários (MinhaReceita: lista de {codigo, descricao})
    secundarios = _primeiro(item, "cnaes_secundarios", "cnaes_secundarias",
                            "secondary_activities", default=[])
    lista_sec = []
    if isinstance(secundarios, list):
        for s in secundarios:
            if isinstance(s, dict):
                cod = str(s.get("codigo") or s.get("code") or "")
                desc = s.get("descricao") or s.get("text") or s.get("description") or ""
                if cod:
                    lista_sec.append({"codigo": formatar_cnae(cod), "descricao": desc})
    emp["cnaes_secundarios"] = lista_sec

    emp["lat"] = _primeiro(item, "lat", "latitude", default=None) or None
    emp["lng"] = _primeiro(item, "lng", "longitude", default=None) or None
    return emp


async def consultar_cnpj(cnpj: str, client: "httpx.AsyncClient | None" = None) -> dict | None:
    """
    Consulta UM CNPJ no MinhaReceita (GET /{cnpj}) e devolve no schema canônico.
    Esta é a parte 100% documentada/estável da API. Usada para ENRIQUECER
    endereço/telefone de CNPJs achados no PNCP. Retorna None se não encontrar.
    """
    digits = "".join(ch for ch in str(cnpj or "") if ch.isdigit())
    if len(digits) != 14:
        return None

    cache_key = "cnpj_" + hashlib.md5(digits.encode()).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[0] if cached else None

    async def _do(c):
        r = await c.get(f"{MINHARECEITA_URL}/{digits}")
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return _normalizar_item(data)

    try:
        if client is not None:
            emp = await _do(client)
        else:
            async with httpx.AsyncClient(
                timeout=TIMEOUT,
                headers={"User-Agent": "OrcamentosPublicos/2.0", "Accept": "application/json"},
            ) as c:
                emp = await _do(c)
    except Exception as e:
        print(f"[Receita CNPJ {digits}] {e}")
        return None

    _cache_set(cache_key, [emp] if emp else [], horas=72)
    return emp


def _extrair_lista(data) -> list[dict]:
    """Extrai a lista de empresas de respostas em formatos variados."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for chave in ("data", "results", "empresas", "cnpj", "items", "records", "estabelecimentos"):
            v = data.get(chave)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                # às vezes vem {"data": {"cnpj": [...]}}
                for chave2 in ("cnpj", "results", "empresas", "items"):
                    vv = v.get(chave2)
                    if isinstance(vv, list):
                        return [x for x in vv if isinstance(x, dict)]
    return []


# ─── Busca no MinhaReceita ─────────────────────────────────────────────────────

async def _buscar_minhareceita(client, cnaes, uf, cod_municipio, cursor=None,
                               limit=50) -> tuple[list[dict], str, str]:
    """
    Busca no MinhaReceita conforme o contrato real da API (código-fonte do
    projeto cuducos/minha-receita):

      GET {URL}/?uf=SP&municipio={códigoIBGE}&cnae_fiscal={num}&limit=N&cursor=...

    - A busca é na raiz "/" (não "/search").
    - 'municipio' é o CÓDIGO IBGE numérico (não o nome).
    - 'cnae_fiscal' é o CNAE principal numérico; repetimos o parâmetro p/ cada CNAE.
    - Resposta: {"data": [...], "cursor": "<prox>" | null}.

    Retorna (lista, proximo_cursor, detalhe_do_erro).
    """
    endpoint = f"{MINHARECEITA_URL}/"
    # httpx aceita lista em um parâmetro → vira ?cnae_fiscal=A&cnae_fiscal=B
    params: dict = {}
    cnaes_num = [c for c in (_cnae_numerico(x) for x in cnaes) if c]
    if cnaes_num:
        params["cnae_fiscal"] = cnaes_num
    if uf:
        params["uf"] = uf
    if cod_municipio:
        params["municipio"] = str(cod_municipio)
    params["limit"] = str(limit)
    if cursor:
        params["cursor"] = cursor

    # Retry com backoff para 429 (limite de requisições) — a instância pública
    # é compartilhada e às vezes retorna 429 passageiro.
    r = None
    for tentativa in range(3):
        try:
            r = await client.get(endpoint, params=params)
        except Exception as e:
            return [], "", f"falha de rede ao acessar {endpoint}: {e}"
        if r.status_code != 429:
            break
        if tentativa < 2:
            await asyncio.sleep(1.5 * (tentativa + 1))  # 1.5s, depois 3s

    if r.status_code == 400:
        return [], "", ("a fonte recusou os filtros (HTTP 400) — verifique UF/município/CNAE")
    if r.status_code == 404:
        return [], "", (f"endpoint {endpoint} não encontrado (HTTP 404) — confira RECEITA_CUSTOM_URL")
    if r.status_code == 408:
        return [], "", "a busca excedeu o tempo limite na fonte (HTTP 408) — tente um filtro mais específico"
    if r.status_code == 429:
        return [], "", "limite de requisições atingido na fonte (HTTP 429) — tente novamente em instantes"
    if r.status_code != 200:
        return [], "", f"a fonte respondeu HTTP {r.status_code}"
    try:
        data = r.json()
    except Exception:
        return [], "", "a fonte respondeu em formato não-JSON"

    lista = _extrair_lista(data)
    prox = ""
    if isinstance(data, dict):
        c = data.get("cursor")
        if isinstance(c, str) and c:
            prox = c
    if not lista:
        return [], prox, "a fonte respondeu, mas sem empresas para este filtro"
    return lista, prox, ""


class ProvedorReceita(ProvedorEmpresas):
    nome = "receita"

    def disponivel(self) -> bool:
        return bool(MINHARECEITA_URL)

    async def buscar(
        self,
        *,
        nome_item: str,
        codigo_cat: str,
        tipo_cat: str,
        tipo_busca: str,
        cidade: str,
        estado: str,
        raio_km: int,
        lat: float | None = None,
        lng: float | None = None,
        max_empresas: int = 50,
        **kwargs,
    ) -> list[dict]:
        cnaes = mapear_cnaes(nome_item, tipo_cat, kwargs.get("descricao", ""))
        uf = (estado or "").strip().upper()
        municipio = (cidade or "").strip()

        # O MinhaReceita filtra município por CÓDIGO IBGE (não pelo nome).
        cod_municipio = None
        if municipio:
            cod_municipio = await ibge.codigo_municipio(municipio, uf)
            if cod_municipio is None and not uf:
                # sem UF e sem conseguir resolver → orienta o usuário
                raise ReceitaIndisponivel(
                    f"Para buscar em '{municipio}', selecione também o Estado (UF)."
                )

        cache_hit = _cache_get(_chave(cnaes, uf, str(cod_municipio or municipio)))
        if cache_hit is not None:
            empresas = await self._aplicar_raio(
                cache_hit, cidade=municipio, uf=uf, raio_km=raio_km,
                lat_centro=lat, lng_centro=lng,
            )
            return empresas[:max_empresas]

        empresas: list[dict] = []
        detalhe_erro = ""
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={"User-Agent": "OrcamentosPublicos/2.0", "Accept": "application/json"},
        ) as client:
            brutos: list[dict] = []
            cursor = None
            for _ in range(3):  # até 3 páginas (cursor)
                lote, cursor, detalhe_erro = await _buscar_minhareceita(
                    client, cnaes, uf, cod_municipio, cursor=cursor, limit=max_empresas
                )
                if not lote:
                    break
                brutos.extend(lote)
                if len(brutos) >= max_empresas or not cursor:
                    break
                await asyncio.sleep(0.3)

            if brutos:
                empresas = [_normalizar_item(b, municipio, uf) for b in brutos]
                _cache_set(_chave(cnaes, uf, str(cod_municipio or municipio)), empresas)

        # Diagnóstico: se não vieram empresas E houve um erro de fonte (não apenas
        # "sem resultados"), avisa o usuário em vez de retornar vazio silencioso.
        if not empresas and detalhe_erro and "sem empresas" not in detalhe_erro:
            raise ReceitaIndisponivel(
                f"Não foi possível consultar a base da Receita (MinhaReceita): {detalhe_erro}."
            )

        # Raio real em km (via OSM/Nominatim)
        empresas = await self._aplicar_raio(
            empresas, cidade=municipio, uf=uf, raio_km=raio_km,
            lat_centro=lat, lng_centro=lng,
        )
        return empresas[:max_empresas]

    async def _aplicar_raio(self, empresas, *, cidade, uf, raio_km,
                            lat_centro=None, lng_centro=None):
        """Filtra as empresas pela distância real ao centro da busca (km)."""
        if not empresas:
            return empresas

        if lat_centro is None or lng_centro is None:
            centro = await geocode.geocodificar(
                f"{cidade}, {uf}, Brasil" if uf else f"{cidade}, Brasil"
            )
            if not centro:
                return empresas
            lat_centro, lng_centro = centro["lat"], centro["lng"]

        por_municipio: dict[str, list[dict]] = {}
        for emp in empresas:
            chave = f"{emp.get('municipio','').strip()}|{(emp.get('uf') or uf).strip()}"
            por_municipio.setdefault(chave, []).append(emp)

        MAX_MUNICIPIOS_GEO = 12
        if len(por_municipio) > MAX_MUNICIPIOS_GEO:
            for grupo in por_municipio.values():
                for emp in grupo:
                    emp["distancia_km"] = None
            return empresas

        for chave, grupo in por_municipio.items():
            mun, ufx = (chave.split("|") + [""])[:2]
            if not mun:
                for emp in grupo:
                    emp["distancia_km"] = None
                continue
            geo = await geocode.geocodificar(
                f"{mun}, {ufx}, Brasil" if ufx else f"{mun}, Brasil"
            )
            await asyncio.sleep(1.0)  # respeita o limite do Nominatim (1 req/s)
            if not geo:
                for emp in grupo:
                    emp["distancia_km"] = None
                continue
            d = round(
                geocode.distancia_km(lat_centro, lng_centro, geo["lat"], geo["lng"]), 1
            )
            for emp in grupo:
                emp["lat"] = emp["lat"] or geo["lat"]
                emp["lng"] = emp["lng"] or geo["lng"]
                emp["distancia_km"] = d

        dentro = [
            e for e in empresas
            if e.get("distancia_km") is None or e["distancia_km"] <= raio_km
        ]
        dentro.sort(key=lambda e: (e.get("distancia_km") is None, e.get("distancia_km") or 0))
        return dentro
