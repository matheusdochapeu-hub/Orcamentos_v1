"""
Provedor de empresas baseado na BASE CNPJ DA RECEITA FEDERAL.

Esta é a ALTERNATIVA ao Google Maps: encontra empresas fornecedoras reais
filtrando por CNAE (atividade econômica) + município + UF. Vantagens:

  * Gratuito e público (dados abertos da Receita Federal).
  * Cobre TODAS as empresas formais do Brasil — inclusive fabricantes,
    distribuidoras e atacadistas que NÃO têm ficha no Google Maps.
  * É a fonte correta para embasar compra pública (CNPJ ativo, CNAE compatível).

Como obtemos os dados:
  Consultar a base bruta da Receita exige baixar dezenas de GB. Em vez disso,
  usamos APIs públicas que já indexam essa base e permitem FILTRAR por
  CNAE + UF + município. O provedor tenta uma lista de backends em ordem e usa
  o primeiro que responder. Todos são configuráveis por variável de ambiente,
  então trocar de fonte não exige mudar código.

Backends suportados (ver RECEITA_BACKEND no .env):
  - "casadosdados" : POST público de busca por filtros (CNAE/UF/município).
  - "cnpja"        : API aberta da CNPJá (cnpja.com) — busca por filtros.
  - "custom"       : endpoint próprio compatível (self-host do minhareceita, etc.)

Nenhuma chave é obrigatória para os backends públicos. Rate limits se aplicam
(ex.: CNPJá open ~5 req/min por IP), por isso há cache local e paginação enxuta.

IMPORTANTE: respostas de APIs externas variam de formato. Por isso a
normalização (`_normalizar_item`) é defensiva e cobre as chaves mais comuns.
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

TIMEOUT = 25.0
CACHE_DB = Path(__file__).resolve().parent.parent.parent / "database" / "catmat_cache.db"

# Endpoints padrão (podem ser sobrescritos por .env)
CASADOSDADOS_URL = os.getenv(
    "RECEITA_CASADOSDADOS_URL",
    "https://api.casadosdados.com.br/v2/public/cnpj/search",
)
CNPJA_URL = os.getenv("RECEITA_CNPJA_URL", "https://api.cnpja.com/office")
CUSTOM_URL = os.getenv("RECEITA_CUSTOM_URL", "")


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


def _chave(backend: str, cnaes: list[str], uf: str, municipio: str) -> str:
    raw = json.dumps(
        {"b": backend, "c": sorted(cnaes), "uf": uf, "m": municipio},
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


def _normalizar_item(item: dict) -> dict:
    """Converte um registro de empresa (formato variado) no schema canônico."""
    emp = empresa_vazia()
    emp["fonte"] = "receita"

    cnpj = str(_primeiro(item, "cnpj", "cnpj_completo", "cnpjCompleto", default="")).strip()
    emp["cnpj"] = cnpj
    emp["id"] = cnpj or _primeiro(item, "id", default="")

    emp["razao_social"] = _primeiro(item, "razao_social", "razaoSocial", "nome", "company", default="")
    emp["nome"] = _primeiro(item, "nome_fantasia", "nomeFantasia", "fantasia",
                            "trade_name", default="") or emp["razao_social"]

    emp["municipio"] = _primeiro(item, "municipio", "cidade", "city", default="")
    emp["uf"] = _primeiro(item, "uf", "estado", "state", default="")
    emp["cep"] = _primeiro(item, "cep", "zip", default="")

    # Monta endereço a partir de partes quando não vier pronto
    endereco = _primeiro(item, "endereco", "logradouro_completo", "address", default="")
    if not endereco:
        partes = [
            _primeiro(item, "logradouro", "street", default=""),
            str(_primeiro(item, "numero", "number", default="")),
            _primeiro(item, "bairro", "district", default=""),
        ]
        endereco = ", ".join(p for p in partes if p)
    cidade_uf = ", ".join(p for p in [emp["municipio"], emp["uf"]] if p)
    emp["endereco"] = ", ".join(p for p in [endereco, cidade_uf] if p)

    emp["telefone"] = _primeiro(item, "telefone", "telefone1", "phone", "ddd_telefone_1", default="")
    emp["email"] = _primeiro(item, "email", "correio_eletronico", default="")
    emp["situacao"] = str(_primeiro(item, "situacao_cadastral", "situacao", "status", default="")).upper()

    cnae = str(_primeiro(item, "cnae_fiscal", "cnae_principal", "cnae", "primary_activity_code", default=""))
    emp["cnae_principal"] = formatar_cnae(cnae)
    emp["cnae_descricao"] = _primeiro(item, "cnae_fiscal_descricao", "cnae_descricao",
                                      "primary_activity", default="")

    # coordenadas raramente vêm da Receita; ficam None (distância fica indisponível)
    emp["lat"] = _primeiro(item, "lat", "latitude", default=None) or None
    emp["lng"] = _primeiro(item, "lng", "longitude", default=None) or None

    return emp


# ─── Backends ──────────────────────────────────────────────────────────────────

async def _buscar_casadosdados(client, cnaes, uf, municipio, pagina=1) -> list[dict]:
    payload = {
        "query": {
            "atividade_principal": cnaes,
            "uf": [uf] if uf else [],
            "municipio": [municipio.upper()] if municipio else [],
            "situacao_cadastral": "ATIVA",
        },
        "extras": {"somente_mei": False, "excluir_mei": False},
        "page": pagina,
    }
    r = await client.post(CASADOSDADOS_URL, json=payload)
    if r.status_code != 200:
        return []
    data = r.json()
    # formatos possíveis: {"data": {"cnpj": [...]}} ou {"cnpj": [...]} ou lista
    cont = data.get("data", data) if isinstance(data, dict) else {}
    itens = (cont.get("cnpj") or cont.get("empresas") or cont.get("results") or [])
    return itens if isinstance(itens, list) else []


async def _buscar_cnpja(client, cnaes, uf, municipio, pagina=1) -> list[dict]:
    params = {
        "state": uf or "",
        "city": municipio or "",
        "activity": ",".join(cnaes[:5]),
        "status": "ACTIVE",
        "limit": 20,
        "page": pagina,
    }
    r = await client.get(CNPJA_URL, params={k: v for k, v in params.items() if v})
    if r.status_code != 200:
        return []
    data = r.json()
    itens = data.get("records") or data.get("data") or data.get("offices") or []
    return itens if isinstance(itens, list) else []


async def _buscar_custom(client, cnaes, uf, municipio, pagina=1) -> list[dict]:
    if not CUSTOM_URL:
        return []
    params = {"cnae": ",".join(cnaes), "uf": uf, "municipio": municipio, "page": pagina}
    r = await client.get(CUSTOM_URL, params=params)
    if r.status_code != 200:
        return []
    data = r.json()
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("empresas") or data.get("results") or []


_BACKENDS = {
    "casadosdados": _buscar_casadosdados,
    "cnpja": _buscar_cnpja,
    "custom": _buscar_custom,
}


class ProvedorReceita(ProvedorEmpresas):
    nome = "receita"

    def disponivel(self) -> bool:
        return True  # backends públicos não exigem chave

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

        # Ordem de backends: .env RECEITA_BACKEND tem prioridade; demais como fallback
        preferido = os.getenv("RECEITA_BACKEND", "casadosdados").strip().lower()
        ordem = [preferido] + [b for b in _BACKENDS if b != preferido]

        cache_hit = _cache_get(_chave(preferido, cnaes, uf, municipio))
        if cache_hit is not None:
            return cache_hit[:max_empresas]

        empresas: list[dict] = []
        async with httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={"User-Agent": "OrcamentosPublicos/2.0", "Accept": "application/json"},
        ) as client:
            for backend in ordem:
                fn = _BACKENDS.get(backend)
                if not fn:
                    continue
                try:
                    brutos: list[dict] = []
                    for pagina in range(1, 3):  # até 2 páginas (enxuto p/ rate limit)
                        lote = await fn(client, cnaes, uf, municipio, pagina)
                        if not lote:
                            break
                        brutos.extend(lote)
                        if len(brutos) >= max_empresas:
                            break
                        await asyncio.sleep(0.4)
                    if brutos:
                        empresas = [_normalizar_item(b) for b in brutos]
                        _cache_set(_chave(backend, cnaes, uf, municipio), empresas)
                        break
                except Exception as e:
                    print(f"[Receita/{backend}] {e}")
                    continue

        # Marca municipio/uf quando a API não devolve (filtro já garante a região)
        for emp in empresas:
            if not emp["municipio"]:
                emp["municipio"] = municipio
            if not emp["uf"]:
                emp["uf"] = uf
        return empresas[:max_empresas]
