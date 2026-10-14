"""
Geocodificação SEM depender do Google Maps.

Converte "cidade, UF" em coordenadas e calcula distâncias. Usado para aplicar
um RAIO REAL em km às empresas da Receita Federal (cujos dados vêm por
município, sem coordenadas).

Estratégia:
  1. Nominatim / OpenStreetMap (gratuito, sem chave) para geocodificar o centro
     da cidade e, quando necessário, o endereço/município de uma empresa.
  2. Cache local em SQLite — o Nominatim pede no máximo 1 req/s e não gosta de
     cargas repetidas, então nunca geocodificamos a mesma string duas vezes.
  3. Fallback: se o Nominatim falhar, a busca continua por município/UF (sem o
     filtro fino de km), em vez de quebrar.

A política de uso do Nominatim exige um User-Agent identificável; definimos um.
"""
from __future__ import annotations

import json
import math
import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
TIMEOUT = 20.0
CACHE_DB = Path(__file__).resolve().parent.parent / "database" / "catmat_cache.db"
USER_AGENT = "OrcamentosPublicos/2.0 (compras publicas; contato via repositorio)"


def _init_cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CACHE_DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS cache_geocode (
               consulta  TEXT PRIMARY KEY,
               lat       REAL,
               lng       REAL,
               endereco  TEXT,
               expira_em TEXT
           )"""
    )
    con.commit()
    con.close()


def _cache_get(consulta: str) -> dict | None:
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        row = con.execute(
            "SELECT lat, lng, endereco, expira_em FROM cache_geocode WHERE consulta = ?",
            (consulta,),
        ).fetchone()
        con.close()
        if not row:
            return None
        lat, lng, endereco, expira = row
        if expira and datetime.fromisoformat(expira) < datetime.now():
            return None
        if lat is None or lng is None:
            return None
        return {"lat": lat, "lng": lng, "endereco_formatado": endereco or ""}
    except Exception:
        return None


def _cache_set(consulta: str, lat, lng, endereco, dias: int = 90):
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        con.execute(
            "INSERT OR REPLACE INTO cache_geocode (consulta, lat, lng, endereco, expira_em) VALUES (?,?,?,?,?)",
            (consulta, lat, lng, endereco, (datetime.now() + timedelta(days=dias)).isoformat()),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def distancia_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distância em km entre dois pontos (Haversine)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def geocodificar(consulta: str, *, pais_br: bool = True) -> dict | None:
    """
    Geocodifica uma string livre (ex.: "Ribeirão Preto, SP" ou um endereço).
    Retorna {"lat","lng","endereco_formatado"} ou None se não encontrar.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return None

    cache = _cache_get(consulta)
    if cache is not None:
        return cache

    params = {
        "q": consulta,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    }
    if pais_br:
        params["countrycodes"] = "br"

    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR"}
        ) as client:
            r = await client.get(NOMINATIM_URL, params=params)
            if r.status_code != 200:
                return None
            dados = r.json()
            if not dados:
                _cache_set(consulta, None, None, "")  # memoiza o "não achou"
                return None
            top = dados[0]
            lat = float(top["lat"])
            lng = float(top["lon"])
            endereco = top.get("display_name", consulta)
            _cache_set(consulta, lat, lng, endereco)
            return {"lat": lat, "lng": lng, "endereco_formatado": endereco}
    except Exception as e:
        print(f"[Geocode] {e}")
        return None


async def geocodificar_cidade(cidade: str, estado: str = "") -> dict:
    """
    Igual a geocodificar, mas voltado ao centro de uma cidade. Lança ValueError
    se não encontrar (mantém compatibilidade com o fluxo antigo do maps_api).
    """
    consulta = f"{cidade}, {estado}, Brasil" if estado else f"{cidade}, Brasil"
    res = await geocodificar(consulta)
    if not res:
        raise ValueError(
            f"Cidade '{cidade}' não encontrada. Verifique o nome e o estado."
        )
    return res
