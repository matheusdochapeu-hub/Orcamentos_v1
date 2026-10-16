"""
Provedor/enriquecedor de empresas via OpenStreetMap (Overpass API).

É a forma GRATUITA de cruzar dados de "mapa" sem depender do Google: a Overpass
API é pública, SEM CHAVE e SEM CARTÃO, e os POIs do OSM trazem nome, telefone,
site, e-mail e horário de funcionamento.

Dois papéis:
  1. DESCOBERTA (ProvedorOSM.buscar): consulta estabelecimentos num raio ao
     redor do centro da cidade e devolve os que casam com o termo do produto.
  2. ENRIQUECIMENTO (enriquecer): recebe empresas já achadas (Receita/PNCP) e
     completa telefone/site/e-mail/horário cruzando por nome + proximidade com
     os POIs do OSM da mesma região.

Boas práticas da Overpass (fair use): no máx. ~1 req/s, timeout curto, cache.
Em qualquer falha, retorna vazio / não altera nada (nunca derruba a busca).

Config (.env):
  OSM_ENABLED=1                                  (0 desliga)
  OVERPASS_URL=https://overpass-api.de/api/interpreter
"""
from __future__ import annotations

import os
import re
import json
import asyncio
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import httpx
from unidecode import unidecode

from services.empresas.base import ProvedorEmpresas, empresa_vazia
from services import geocode

OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
OSM_ENABLED = os.getenv("OSM_ENABLED", "1").strip() not in ("0", "false", "False", "")
TIMEOUT = 35.0
CACHE_DB = Path(__file__).resolve().parent.parent.parent / "database" / "catmat_cache.db"

# Tags OSM que costumam representar fornecedores de materiais/serviços.
# Buscamos POIs com qualquer uma destas chaves e filtramos pelo nome depois.
_TAGS_COMERCIO = ["shop", "office", "craft"]
_TAGS_SERVICO = ["office", "craft", "amenity"]

_STOP = {"de","da","do","dos","das","para","com","por","em","um","uma","e","a","o",
         "ltda","me","epp","eireli","sa","s","cia","comercio","servico","servicos"}


def _norm(s: str) -> str:
    s = unidecode(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _palavras(s: str) -> list[str]:
    return [p for p in _norm(s).split() if len(p) >= 4 and p not in _STOP]


# ─── Cache ──────────────────────────────────────────────────────────────────

def _init_cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CACHE_DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS cache_osm (
               chave TEXT PRIMARY KEY, dados TEXT NOT NULL, expira_em TEXT NOT NULL
           )"""
    )
    con.commit(); con.close()


def _cache_get(chave: str):
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        row = con.execute("SELECT dados, expira_em FROM cache_osm WHERE chave=?", (chave,)).fetchone()
        con.close()
        if not row:
            return None
        dados, expira = row
        if datetime.fromisoformat(expira) < datetime.now():
            return None
        return json.loads(dados)
    except Exception:
        return None


def _cache_set(chave: str, dados, horas: int = 24):
    try:
        _init_cache()
        con = sqlite3.connect(CACHE_DB)
        con.execute("INSERT OR REPLACE INTO cache_osm (chave, dados, expira_em) VALUES (?,?,?)",
                    (chave, json.dumps(dados, ensure_ascii=False),
                     (datetime.now() + timedelta(hours=horas)).isoformat()))
        con.commit(); con.close()
    except Exception:
        pass


def _chave(prefixo: str, *partes) -> str:
    raw = json.dumps([prefixo, *partes], sort_keys=True)
    return "osm_" + hashlib.md5(raw.encode()).hexdigest()


# ─── Overpass ───────────────────────────────────────────────────────────────

def _montar_query(lat: float, lng: float, raio_m: int, chaves: list[str]) -> str:
    """Monta a Overpass QL: nós/ways/relations com as chaves, no raio."""
    partes = []
    for ch in chaves:
        for tipo in ("node", "way", "relation"):
            partes.append(f'{tipo}["{ch}"](around:{raio_m},{lat},{lng});')
    corpo = "".join(partes)
    return f"[out:json][timeout:25];({corpo});out center tags 200;"


def _poi_para_empresa(el: dict) -> dict:
    tags = el.get("tags", {}) or {}
    emp = empresa_vazia()
    emp["fonte"] = "osm"
    emp["id"] = f'osm:{el.get("type","")}/{el.get("id","")}'
    emp["nome"] = tags.get("name", "") or tags.get("operator", "")
    emp["razao_social"] = emp["nome"]

    emp["telefone"] = (tags.get("phone") or tags.get("contact:phone") or "").strip()
    emp["telefone2"] = (tags.get("contact:mobile") or tags.get("mobile") or "").strip()
    emp["email"] = (tags.get("email") or tags.get("contact:email") or "").strip()
    emp["site"] = (tags.get("website") or tags.get("contact:website") or "").strip()
    emp["horario"] = tags.get("opening_hours", "")

    rua = tags.get("addr:street", "")
    num = tags.get("addr:housenumber", "")
    bairro = tags.get("addr:suburb", "") or tags.get("addr:neighbourhood", "")
    cidade = tags.get("addr:city", "")
    uf = tags.get("addr:state", "")
    partes = [p for p in [f"{rua} {num}".strip(), bairro, cidade, uf] if p]
    emp["municipio"] = cidade
    emp["uf"] = uf
    emp["endereco"] = ", ".join(partes)

    # coordenadas (node tem lat/lon; way/relation tem center)
    if el.get("lat") is not None:
        emp["lat"], emp["lng"] = el.get("lat"), el.get("lon")
    elif el.get("center"):
        emp["lat"], emp["lng"] = el["center"].get("lat"), el["center"].get("lon")

    # categoria legível (shop=..., craft=..., office=...)
    for ch in ("shop", "craft", "office", "amenity"):
        if tags.get(ch):
            emp["cnae_descricao"] = f"{ch}={tags[ch]}"
            break
    return emp


async def _consultar_overpass(client, lat, lng, raio_m, chaves) -> list[dict]:
    q = _montar_query(lat, lng, raio_m, chaves)
    try:
        r = await client.post(OVERPASS_URL, data={"data": q})
    except Exception as e:
        print(f"[OSM] rede: {e}")
        return []
    if r.status_code != 200:
        print(f"[OSM] HTTP {r.status_code}")
        return []
    try:
        return r.json().get("elements", []) or []
    except Exception:
        return []


# ─── DESCOBERTA ──────────────────────────────────────────────────────────────

class ProvedorOSM(ProvedorEmpresas):
    nome = "osm"

    def disponivel(self) -> bool:
        return OSM_ENABLED

    async def buscar(self, *, nome_item, codigo_cat, tipo_cat, tipo_busca,
                     cidade, estado, raio_km, lat=None, lng=None,
                     max_empresas=50, **kwargs) -> list[dict]:
        if not OSM_ENABLED:
            return []
        palavras_item = _palavras(f"{nome_item} {kwargs.get('descricao','')}")
        if not palavras_item:
            return []

        # centro da busca
        if lat is None or lng is None:
            centro = await geocode.geocodificar(
                f"{cidade}, {estado}, Brasil" if estado else f"{cidade}, Brasil"
            )
            if not centro:
                return []
            lat, lng = centro["lat"], centro["lng"]

        raio_m = min(max(raio_km, 1), 100) * 1000  # Overpass: limita a 100km p/ não estourar
        chaves = _TAGS_COMERCIO if tipo_cat.upper() in ("CATMAT", "MATERIAL") else _TAGS_SERVICO

        ck = _chave("busca", round(lat, 3), round(lng, 3), raio_m, sorted(chaves))
        elementos = _cache_get(ck)
        if elementos is None:
            async with httpx.AsyncClient(
                timeout=TIMEOUT, headers={"User-Agent": "OrcamentosPublicos/2.0"}
            ) as client:
                elementos = await _consultar_overpass(client, lat, lng, raio_m, chaves)
            _cache_set(ck, elementos)

        # filtra POIs cujo nome/categoria casa com as palavras do item
        achados = []
        for el in elementos:
            tags = el.get("tags", {}) or {}
            if not tags.get("name"):
                continue
            texto = _norm(" ".join([
                tags.get("name", ""), tags.get("shop", ""), tags.get("craft", ""),
                tags.get("office", ""), tags.get("amenity", ""),
            ]))
            if not any(p in texto for p in palavras_item):
                continue
            emp = _poi_para_empresa(el)
            # distância real ao centro
            if emp.get("lat") and emp.get("lng"):
                d = geocode.distancia_km(lat, lng, emp["lat"], emp["lng"])
                emp["distancia_km"] = round(d, 1)
            achados.append(emp)

        achados.sort(key=lambda e: (e.get("distancia_km") is None, e.get("distancia_km") or 0))
        return achados[:max_empresas]


# ─── ENRIQUECIMENTO ──────────────────────────────────────────────────────────

def _assinatura_nome(nome: str) -> set:
    """Conjunto de palavras significativas do nome (p/ casar empresas)."""
    return set(_palavras(nome))


async def enriquecer(empresas: list[dict], *, cidade: str, estado: str,
                     lat=None, lng=None, raio_km: int = 50) -> list[dict]:
    """
    Completa telefone/site/e-mail/horário das empresas usando POIs do OSM da
    região (casando por nome + proximidade). Não cria empresas novas aqui; só
    preenche o que estiver vazio. Degrada em silêncio.
    """
    if not OSM_ENABLED or not empresas:
        return empresas

    # Só vale a pena buscar o mapa se há empresas SEM telefone (ou sem site).
    faltando = [e for e in empresas if not (e.get("telefone") or "").strip()]
    if not faltando:
        return empresas

    if lat is None or lng is None:
        centro = await geocode.geocodificar(
            f"{cidade}, {estado}, Brasil" if estado else f"{cidade}, Brasil"
        )
        if not centro:
            return empresas
        lat, lng = centro["lat"], centro["lng"]

    raio_m = min(max(raio_km, 1), 100) * 1000
    chaves = ["shop", "office", "craft", "amenity"]
    ck = _chave("enriq", round(lat, 3), round(lng, 3), raio_m)
    elementos = _cache_get(ck)
    if elementos is None:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, headers={"User-Agent": "OrcamentosPublicos/2.0"}
        ) as client:
            elementos = await _consultar_overpass(client, lat, lng, raio_m, chaves)
        _cache_set(ck, elementos)

    # índice de POIs por assinatura de nome
    pois = []
    for el in elementos:
        tags = el.get("tags", {}) or {}
        nome = tags.get("name", "")
        if not nome:
            continue
        pois.append((_assinatura_nome(nome), _poi_para_empresa(el)))

    if not pois:
        return empresas

    for emp in empresas:
        if (emp.get("telefone") or "").strip() and (emp.get("site") or "").strip():
            continue
        alvo = _assinatura_nome(emp.get("nome", "") or emp.get("razao_social", ""))
        if not alvo:
            continue
        melhor = None
        melhor_score = 0.0
        for sig, poi in pois:
            if not sig:
                continue
            inter = len(alvo & sig)
            if inter == 0:
                continue
            score = inter / max(len(alvo), 1)
            if score > melhor_score:
                melhor_score, melhor = score, poi
        # exige casamento forte de nome (>=60% das palavras significativas)
        if melhor and melhor_score >= 0.6:
            for campo in ("telefone", "telefone2", "email", "site", "horario"):
                if not (emp.get(campo) or "").strip() and (melhor.get(campo) or "").strip():
                    emp[campo] = melhor[campo]
            emp["enriquecido_osm"] = True

    return empresas
