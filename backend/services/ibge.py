"""
Resolução de código de município via API de Localidades do IBGE.

Por quê: a busca do MinhaReceita filtra município pelo **código IBGE numérico**
(ex.: São Carlos/SP = 3548906), não pelo nome. Este módulo converte
"nome da cidade + UF" no código IBGE correspondente.

Fonte: https://servicodados.ibge.gov.br/api/v1/localidades  (pública, sem chave)

Estratégia:
  - Busca os municípios da UF uma vez e casa pelo nome (normalizado, sem acento).
  - Cacheia em SQLite: tanto a lista por UF quanto o código já resolvido.
  - Se a API do IBGE falhar, retorna None (o chamador segue sem o filtro por
    código — melhor degradar do que quebrar).
"""
from __future__ import annotations

import re
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import httpx
from unidecode import unidecode

from database.paths import db_path

IBGE_MUNICIPIOS_UF = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
TIMEOUT = 20.0
CACHE_DB = db_path("catmat_cache.db")


def _norm(s: str) -> str:
    s = unidecode(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _init_cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CACHE_DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS cache_ibge_uf (
               uf        TEXT PRIMARY KEY,
               dados     TEXT NOT NULL,
               expira_em TEXT NOT NULL
           )"""
    )
    con.commit()
    con.close()


def _cache_uf_get(uf: str) -> list[dict] | None:
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        row = con.execute(
            "SELECT dados, expira_em FROM cache_ibge_uf WHERE uf = ?", (uf,)
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


def _cache_uf_set(uf: str, dados: list[dict], dias: int = 180):
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        con.execute(
            "INSERT OR REPLACE INTO cache_ibge_uf (uf, dados, expira_em) VALUES (?,?,?)",
            (uf, json.dumps(dados, ensure_ascii=False),
             (datetime.now() + timedelta(days=dias)).isoformat()),
        )
        con.commit()
        con.close()
    except Exception:
        pass


async def _municipios_da_uf(uf: str) -> list[dict]:
    """Retorna [{'id': 3548906, 'nome': 'São Carlos'}, ...] para a UF."""
    uf = (uf or "").strip().upper()
    if not uf:
        return []
    cache = _cache_uf_get(uf)
    if cache is not None:
        return cache
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, headers={"Accept": "application/json"}
        ) as client:
            r = await client.get(IBGE_MUNICIPIOS_UF.format(uf=uf))
            if r.status_code != 200:
                return []
            dados = r.json()
            lista = [{"id": m.get("id"), "nome": m.get("nome", "")} for m in dados if m.get("id")]
            if lista:
                _cache_uf_set(uf, lista)
            return lista
    except Exception as e:
        print(f"[IBGE {uf}] {e}")
        return []


async def listar_municipios(uf: str) -> list[str]:
    """Lista (ordenada) os nomes de municípios de uma UF. [] se falhar."""
    municipios = await _municipios_da_uf(uf)
    nomes = sorted((m.get("nome", "") for m in municipios if m.get("nome")),
                   key=lambda n: _norm(n))
    return nomes


async def codigo_municipio(cidade: str, uf: str) -> int | None:
    """
    Converte 'cidade' + 'uf' no código IBGE (int). None se não encontrar.
    Casa por nome normalizado (sem acento/caixa); tolera pequenas variações.
    """
    cidade_n = _norm(cidade)
    if not cidade_n:
        return None
    municipios = await _municipios_da_uf(uf)
    if not municipios:
        return None
    # 1) match exato normalizado
    for m in municipios:
        if _norm(m["nome"]) == cidade_n:
            return int(m["id"])
    # 2) match por início (ex.: usuário digitou parte do nome)
    candidatos = [m for m in municipios if _norm(m["nome"]).startswith(cidade_n)]
    if len(candidatos) == 1:
        return int(candidatos[0]["id"])
    # 3) match por conter
    candidatos = [m for m in municipios if cidade_n in _norm(m["nome"])]
    if len(candidatos) == 1:
        return int(candidatos[0]["id"])
    return None
